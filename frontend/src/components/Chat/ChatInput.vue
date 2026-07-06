<template>
  <div class="input-area-wrapper">
    <div class="input-area">
      <button class="attach-btn"><i class="fas fa-paperclip"></i></button>
      
      <textarea 
        v-model="chatStore.userInput" 
        @keydown="handleKeyDown"
        @compositionstart="handleCompositionStart"
        @compositionend="handleCompositionEnd"
        @input="autoResize"
        placeholder="问一道菜、一个食材，或输入菜单文件/项目路径... (Shift+Enter 换行)"
        rows="1"
        ref="textareaRef"
      ></textarea>
      
      <button 
        v-if="chatStore.isLoading" 
        @click="chatStore.handleStop" 
        class="send-btn stop-btn" 
        title="终止回答"
      >
        <i class="fas fa-stop"></i>
      </button>
      
      <button 
        v-else 
        @click="onSend" 
        class="send-btn" 
        title="发送"
      >
        <i class="fas fa-paper-plane"></i>
      </button>
    </div>
    <div class="footer-text">迷你烹饪问答机器人会结合工具结果回答，重要结论仍建议你核对来源。</div>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick } from 'vue';
import { useChatStore } from '@/stores/chat';

const chatStore = useChatStore();
const textareaRef = ref<HTMLTextAreaElement | null>(null);
const isComposing = ref(false);

const handleCompositionStart = () => {
  isComposing.value = true;
};

const handleCompositionEnd = () => {
  isComposing.value = false;
};

const handleKeyDown = (event: KeyboardEvent) => {
  if (event.key === 'Enter' && !event.shiftKey && !isComposing.value) {
    event.preventDefault();
    onSend();
  }
};

const autoResize = () => {
  if (textareaRef.value) {
    textareaRef.value.style.height = 'auto';
    textareaRef.value.style.height = textareaRef.value.scrollHeight + 'px';
  }
};

const resetTextareaHeight = () => {
  if (textareaRef.value) {
    textareaRef.value.style.height = 'auto';
  }
};

const onSend = async () => {
  const text = chatStore.userInput.trim();
  if (!text || chatStore.isLoading || isComposing.value) return;

  await chatStore.handleSend();
  
  await nextTick();
  resetTextareaHeight();
};
</script>
