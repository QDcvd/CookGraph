<template>
  <div class="chat-area">
    <header class="chat-header">
      <div class="header-info">
        <div class="status-dot"></div>
        <span>迷你烹饪问答机器人已就绪</span>
      </div>
      <div class="header-mode">COOK MODE</div>
    </header>

    <div class="chat-container" ref="chatContainerRef" @scroll="handleScroll">
      <WelcomeScreen v-if="chatStore.messages.length === 0" />
      
      <!-- Messages List -->
      <MessageItem 
        v-for="(msg, index) in chatStore.messages" 
        :key="index" 
        :msg="msg" 
        :msg-index="index" 
        :ref="(el) => { if (el) messageItemRefs[index] = el; }"
        @cite-click="scrollToChunk"
      />
    </div>

    <!-- Bottom Input Area -->
    <ChatInput />
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick, onBeforeUpdate, onMounted } from 'vue';
import WelcomeScreen from './WelcomeScreen.vue';
import MessageItem from './MessageItem.vue';
import ChatInput from './ChatInput.vue';
import { useChatStore } from '@/stores/chat';

const chatStore = useChatStore();
const chatContainerRef = ref<HTMLDivElement | null>(null);
const messageItemRefs = ref<any[]>([]);
const shouldAutoScroll = ref(true);

onBeforeUpdate(() => {
  messageItemRefs.value = [];
});

const scrollToBottom = () => {
  if (chatContainerRef.value) {
    chatContainerRef.value.scrollTop = chatContainerRef.value.scrollHeight;
  }
};

const isNearBottom = () => {
  const el = chatContainerRef.value;
  if (!el) return true;
  return el.scrollHeight - el.scrollTop - el.clientHeight < 120;
};

const handleScroll = () => {
  shouldAutoScroll.value = isNearBottom();
};

const scrollToChunk = async (msgIndex: number, chunkIndex: number) => {
  const msgItem = messageItemRefs.value[msgIndex];
  if (!msgItem) return;

  // Expand References section
  msgItem.openReferences();

  await nextTick();
  const chunkEl = document.getElementById(`chunk-${msgIndex}-${chunkIndex}`);
  if (chunkEl) {
    chunkEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
    chunkEl.classList.add('highlight-chunk');
    setTimeout(() => {
      chunkEl.classList.remove('highlight-chunk');
    }, 2000);
  }
};

// Follow streaming only while the user is already near the bottom.
watch(
  () => chatStore.messages,
  () => {
    nextTick(() => {
      if (shouldAutoScroll.value) {
        scrollToBottom();
      }
    });
  },
  { deep: true }
);

watch(
  () => chatStore.messages.length,
  () => {
    shouldAutoScroll.value = true;
    nextTick(() => {
      scrollToBottom();
    });
  }
);

onMounted(() => {
  scrollToBottom();
});
</script>
