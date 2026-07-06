<template>
  <div :class="['message', msg.isUser ? 'user-message' : 'bot-message']">
    <!-- User or finished AI answer -->
    <template v-if="msg.isUser">
      <MessageContent 
        :text="msg.text" 
        :is-user="true" 
        :msg-index="msgIndex" 
      />
    </template>
    
    <template v-else>
      <!-- RAG Thinking/Trace view -->
      <ThinkingTrace 
        v-if="msg.isThinking && !msg.text" 
        :msg="msg" 
        :msg-index="msgIndex" 
      />
      
      <!-- Process and final answer are intentionally separated. -->
      <template v-else>
        <RetrievalTraceDetails :msg="msg" />

        <section class="final-answer">
          <div class="final-answer-label">
            <i class="fas fa-comment-dots"></i>
            <span>最终回答</span>
          </div>

          <MessageContent 
            :text="msg.text" 
            :is-user="false" 
            :msg-index="msgIndex" 
            @cite-click="onCiteClick"
          />
        </section>

        <References 
          ref="referencesRef"
          :msg="msg" 
          :msg-index="msgIndex" 
          @cite-click="onCiteClick"
        />
      </template>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import MessageContent from './MessageContent.vue';
import ThinkingTrace from './ThinkingTrace.vue';
import References from './References.vue';
import RetrievalTraceDetails from './RetrievalTraceDetails.vue';
import type { Message } from '@/types/chat';

const props = defineProps<{
  msg: Message;
  msgIndex: number;
}>();

const emit = defineEmits<{
  (e: 'cite-click', msgIndex: number, chunkIndex: number): void;
}>();

const referencesRef = ref<InstanceType<typeof References> | null>(null);

const openReferences = () => {
  referencesRef.value?.openDetails();
};

defineExpose({
  openReferences
});

const onCiteClick = (msgIndex: number, chunkIndex: number) => {
  emit('cite-click', msgIndex, chunkIndex);
};
</script>
