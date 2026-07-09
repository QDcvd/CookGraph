import { defineStore } from 'pinia';
import { useSessionStore } from './sessions';
import api from '@/utils/api';
import type { Message, RagStep, GroupedRagStep } from '@/types/chat';

export const useChatStore = defineStore('chat', {
  state: () => ({
    messages: [] as Message[],
    userInput: '',
    isLoading: false,
    activeNav: 'newChat' as 'newChat' | 'history' | 'settings',
    sessionId: 'session_' + Date.now(),
    abortController: null as AbortController | null,
    focusInputRequestedAt: 0,
  }),

  actions: {
    appendRagStepToGroups(prev: GroupedRagStep[], step: RagStep): GroupedRagStep[] {
      const groups = prev ? [...prev] : [];
      const g = step.group || null;
      
      if (g) {
        const idx = groups.findIndex((grp) => grp.group === g);
        if (idx >= 0) {
          const existing = groups[idx];
          const updated: GroupedRagStep = {
            group: existing.group,
            label: existing.label,
            steps: [...existing.steps, step],
            collapsed: existing.collapsed,
          };
          groups[idx] = updated;
          return groups;
        }
        return [...groups, { group: g, label: g, steps: [step], collapsed: true }];
      }

      const last = groups.length > 0 ? groups[groups.length - 1] : null;
      if (last && last.group === null) {
        const updated = { ...last, steps: [...last.steps, step] };
        groups[groups.length - 1] = updated;
        return groups;
      }
      return [...groups, { group: null, label: null, steps: [step], collapsed: false }];
    },

    groupRagSteps(steps: RagStep[]): GroupedRagStep[] {
      if (!steps || !steps.length) return [];
      return steps.reduce((groups: GroupedRagStep[], step) => this.appendRagStepToGroups(groups, step), []);
    },

    toggleStepGroup(msgIndex: number, groupIndex: number) {
      const msg = this.messages[msgIndex];
      if (!msg || !msg._groupedSteps || !msg._groupedSteps[groupIndex]) return;
      msg._groupedSteps[groupIndex].collapsed = !msg._groupedSteps[groupIndex].collapsed;
    },

    handleNewChat() {
      this.messages = [];
      this.sessionId = 'session_' + Date.now();
      this.activeNav = 'newChat';
      const sessionStore = useSessionStore();
      sessionStore.showHistorySidebar = false;
    },

    handleClearChat() {
      if (confirm('确定要清空当前对话吗？')) {
        this.messages = [];
      }
    },

    async loadSession(sessionId: string) {
      this.sessionId = sessionId;
      this.activeNav = 'newChat';
      const sessionStore = useSessionStore();
      sessionStore.showHistorySidebar = false;

      try {
        const response = await api.get(`/sessions/${encodeURIComponent(sessionId)}`);
        const data = response.data;
        this.messages = (data.messages || []).map((msg: any) => ({
          text: msg.content,
          isUser: msg.type === 'human',
          ragTrace: msg.rag_trace || null,
          tokenUsage: msg.rag_trace?.token_usage || null,
          choicePrompt: msg.rag_trace?.choice_prompt || null,
          selectedChoiceKey: null,
        }));
      } catch (error: any) {
        const errMsg = error.response?.data?.detail || error.message || '加载会话失败';
        this.messages = [];
        throw new Error(errMsg);
      }
    },

    handleStop() {
      if (this.abortController) {
        this.abortController.abort();
      }
    },

    requestInputFocus() {
      this.focusInputRequestedAt = Date.now();
    },

    async choosePromptOption(msgIndex: number, optionKey: string) {
      const msg = this.messages[msgIndex];
      if (!msg || msg.isUser || !msg.choicePrompt || msg.selectedChoiceKey || this.isLoading) return;

      const latestAssistantIndex = [...this.messages]
        .map((item, index) => ({ item, index }))
        .reverse()
        .find(({ item }) => !item.isUser)?.index;
      if (latestAssistantIndex !== msgIndex) return;

      const option = msg.choicePrompt.options.find((item) => String(item.key) === String(optionKey));
      if (!option) return;

      msg.selectedChoiceKey = option.key;
      if (option.custom) {
        this.requestInputFocus();
        return;
      }

      const sendText = (option.send_text || '').trim();
      if (!sendText) {
        this.requestInputFocus();
        return;
      }

      this.userInput = sendText;
      await this.handleSend();
    },

    async handleSend() {
      const sessionStore = useSessionStore();

      const text = this.userInput.trim();
      if (!text || this.isLoading) return;

      this.messages.push({
        text: text,
        isUser: true,
      });

      if (this.messages.length === 1) {
        const tempTitle = text.length > 10 ? text.substring(0, 10) + '...' : text;
        const existingSession = sessionStore.sessions.find((s) => s.session_id === this.sessionId);
        if (!existingSession) {
          sessionStore.sessions.unshift({
            session_id: this.sessionId,
            title: tempTitle,
            message_count: 1,
            updated_at: new Date().toISOString(),
          });
        }
      }

      this.userInput = '';
      this.isLoading = true;

      this.messages.push({
        text: '',
        isUser: false,
        isThinking: true,
        ragTrace: null,
        ragSteps: [],
        _groupedSteps: [],
        tokenUsage: null,
        choicePrompt: null,
        selectedChoiceKey: null,
      });
      const botMsgIdx = this.messages.length - 1;

      this.abortController = new AbortController();

      try {
        const response = await fetch('/chat/stream', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            message: text,
            session_id: this.sessionId,
          }),
          signal: this.abortController.signal,
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const reader = response.body?.getReader();
        if (!reader) throw new Error('无法读取响应流');

        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });

          let eventEndIndex;
          while ((eventEndIndex = buffer.indexOf('\n\n')) !== -1) {
            const eventStr = buffer.slice(0, eventEndIndex);
            buffer = buffer.slice(eventEndIndex + 2);

            if (eventStr.startsWith('data: ')) {
              const dataStr = eventStr.slice(6);
              if (dataStr === '[DONE]') continue;
              try {
                const data = JSON.parse(dataStr);
                if (data.type === 'content') {
                  if (this.messages[botMsgIdx].isThinking) {
                    this.messages[botMsgIdx].isThinking = false;
                  }
                  this.messages[botMsgIdx].text += data.content;
                } else if (data.type === 'thinking') {
                  // 本地模型的推理过程，存入 ragSteps 可折叠展示
                  const msg = this.messages[botMsgIdx];
                  if (!msg.ragSteps) msg.ragSteps = [];
                  msg.ragSteps.push({ label: data.content, icon: '🧠' });
                  msg._groupedSteps = this.appendRagStepToGroups(msg._groupedSteps || [], { label: data.content, icon: '🧠' });
                } else if (data.type === 'trace') {
                  this.messages[botMsgIdx].ragTrace = data.rag_trace;
                  this.messages[botMsgIdx].tokenUsage = data.rag_trace?.token_usage || this.messages[botMsgIdx].tokenUsage || null;
                  this.messages[botMsgIdx].choicePrompt = data.rag_trace?.choice_prompt || null;
                } else if (data.type === 'token_usage') {
                  this.messages[botMsgIdx].tokenUsage = data.token_usage;
                  if (this.messages[botMsgIdx].ragTrace) {
                    this.messages[botMsgIdx].ragTrace.token_usage = data.token_usage;
                  }
                } else if (data.type === 'rag_step') {
                  const msg = this.messages[botMsgIdx];
                  if (!msg.ragSteps) msg.ragSteps = [];
                  msg.ragSteps.push(data.step);
                  msg._groupedSteps = this.appendRagStepToGroups(msg._groupedSteps || [], data.step);
                } else if (data.type === 'session_title') {
                  const s = sessionStore.sessions.find(
                    (item) => item.session_id === data.session_id
                  );
                  if (s) {
                    s.title = data.title;
                    s.updated_at = new Date().toISOString();
                    s.message_count = this.messages.length;
                  } else {
                    sessionStore.sessions.unshift({
                      session_id: data.session_id,
                      title: data.title,
                      message_count: this.messages.length,
                      updated_at: new Date().toISOString(),
                    });
                  }
                } else if (data.type === 'error') {
                  this.messages[botMsgIdx].isThinking = false;
                  this.messages[botMsgIdx].text += `\n[Error: ${data.content}]`;
                }
              } catch (e) {
                console.warn('SSE parse error:', e);
              }
            }
          }
        }
      } catch (error: any) {
        if (error.name === 'AbortError') {
          this.messages[botMsgIdx].isThinking = false;
          if (!this.messages[botMsgIdx].text) {
            this.messages[botMsgIdx].text = '(已终止回答)';
          } else {
            this.messages[botMsgIdx].text += '\n\n_(回答已被终止)_';
          }
        } else {
          this.messages[botMsgIdx].isThinking = false;
          this.messages[botMsgIdx].text = `出了点问题：${error.message}`;
        }
      } finally {
        this.isLoading = false;
        this.abortController = null;
      }
    },
  },
});
