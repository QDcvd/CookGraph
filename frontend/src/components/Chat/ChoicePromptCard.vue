<template>
  <section v-if="prompt" class="choice-prompt-card" aria-label="需要用户确认">
    <div class="choice-prompt-question">
      <i class="fas fa-list-check"></i>
      <span>{{ prompt.question }}</span>
    </div>

    <div class="choice-options">
      <button
        v-for="option in prompt.options"
        :key="option.key"
        class="choice-option-btn"
        :class="{ selected: msg.selectedChoiceKey === option.key }"
        :disabled="isDisabled"
        :title="option.custom ? '点击后在输入框补充说明' : `发送：${option.send_text || option.label}`"
        @click="chatStore.choosePromptOption(msgIndex, option.key)"
      >
        <span class="choice-key">{{ option.key }}</span>
        <span class="choice-label">{{ option.label }}</span>
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useChatStore } from '@/stores/chat';
import type { Message } from '@/types/chat';

const props = defineProps<{
  msg: Message;
  msgIndex: number;
}>();

const chatStore = useChatStore();

const prompt = computed(() => props.msg.choicePrompt || null);

const latestAssistantIndex = computed(() => {
  for (let index = chatStore.messages.length - 1; index >= 0; index -= 1) {
    if (!chatStore.messages[index].isUser) return index;
  }
  return -1;
});

const isDisabled = computed(() => {
  return Boolean(
    props.msg.selectedChoiceKey ||
    chatStore.isLoading ||
    props.msgIndex !== latestAssistantIndex.value
  );
});
</script>

<style scoped>
.choice-prompt-card {
  margin-top: 10px;
  padding: 12px;
  border: 2px solid #2b2118;
  border-radius: 8px;
  background: #fff7df;
  box-shadow: 3px 3px 0 rgba(43, 33, 24, 0.18);
}

.choice-prompt-question {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #4a3322;
  font-size: 0.92rem;
  font-weight: 700;
  line-height: 1.35;
  margin-bottom: 10px;
}

.choice-options {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.choice-option-btn {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-height: 36px;
  padding: 7px 11px;
  border: 2px solid #2b2118;
  border-radius: 8px;
  background: #fffaf0;
  color: #2b2118;
  box-shadow: 2px 2px 0 rgba(43, 33, 24, 0.25);
  cursor: pointer;
  font: inherit;
  font-weight: 700;
  transition: transform 0.12s ease, box-shadow 0.12s ease, background 0.12s ease;
}

.choice-option-btn:hover:not(:disabled) {
  transform: translateY(-1px);
  background: #ffe6a8;
  box-shadow: 3px 3px 0 rgba(43, 33, 24, 0.3);
}

.choice-option-btn:disabled {
  cursor: default;
  opacity: 0.62;
}

.choice-option-btn.selected {
  background: #ffb347;
  opacity: 1;
}

.choice-key {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #2b2118;
  color: #fffaf0;
  font-size: 0.78rem;
}

.choice-label {
  white-space: normal;
  overflow-wrap: anywhere;
}

@media (max-width: 640px) {
  .choice-options {
    flex-direction: column;
  }

  .choice-option-btn {
    width: 100%;
    justify-content: flex-start;
  }
}
</style>
