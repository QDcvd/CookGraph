<template>
  <div v-if="shouldShow" class="process-panel">
    <details class="process-details">
      <summary class="process-summary">
        <span class="process-summary-main">
          <i class="fas fa-route"></i>
          <span>{{ title }}</span>
        </span>
        <span class="process-summary-meta">{{ summaryText }}</span>
      </summary>

      <div class="process-content">
        <div class="process-status">
          <span class="process-pill">{{ modeLabel }}</span>
          <span v-if="toolName">工具：{{ toolName }}</span>
          <span v-else>当前未启用 RAG 检索</span>
        </div>

        <ol v-if="steps.length" class="process-steps">
          <li v-for="(step, index) in steps" :key="index" class="process-step">
            <span class="process-step-icon">{{ step.icon || '•' }}</span>
            <span class="process-step-text">{{ step.label }}</span>
            <span v-if="step.detail" class="process-step-detail">{{ step.detail }}</span>
          </li>
        </ol>

        <div v-if="hasRetrievalMeta" class="process-grid">
          <div v-if="trace?.retrieval_stage">
            <b>阶段</b>
            <span>{{ trace.retrieval_stage }}</span>
          </div>
          <div v-if="trace?.retrieval_mode">
            <b>模式</b>
            <span>{{ trace.retrieval_mode }}</span>
          </div>
          <div v-if="trace?.retrieval_top_k !== undefined">
            <b>Top K</b>
            <span>{{ trace.retrieval_top_k }}</span>
          </div>
          <div v-if="trace?.rerank_model">
            <b>Rerank</b>
            <span>{{ trace.rerank_model }}</span>
          </div>
        </div>

        <div v-if="chunks.length" class="process-sources">
          <div class="process-section-title">检索片段</div>
          <ul>
            <li v-for="(chunk, index) in chunks" :key="index">
              <b>{{ chunk.filename || `片段 ${index + 1}` }}</b>
              <span v-if="chunk.page_number">第 {{ chunk.page_number }} 页</span>
              <p v-if="chunk.text">{{ chunk.text }}</p>
            </li>
          </ul>
        </div>

        <p v-if="!steps.length && !chunks.length && !hasRetrievalMeta" class="process-empty">
          本轮只有普通对话结果。这里会保留给后续 RAG：召回文档、重写 query、rerank 和引用来源都会显示在这里。
        </p>
      </div>
    </details>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import type { Message, RetrievedChunk } from '@/types/chat';

const props = defineProps<{
  msg: Message;
}>();

const trace = computed(() => props.msg.ragTrace || null);
const steps = computed(() => props.msg.ragSteps || []);

const chunks = computed<RetrievedChunk[]>(() => {
  const value = trace.value;
  if (!value) return [];
  return [
    ...(value.initial_retrieved_chunks || []),
    ...(value.retrieved_chunks || []),
    ...(value.expanded_retrieved_chunks || []),
  ];
});

const toolName = computed(() => {
  if (!trace.value?.tool_used) return '';
  return trace.value.tool_name || 'find_tool';
});

const hasRetrievalMeta = computed(() => {
  const value = trace.value;
  if (!value) return false;
  return Boolean(
    value.retrieval_stage ||
    value.retrieval_mode ||
    value.retrieval_top_k !== undefined ||
    value.rerank_model
  );
});

const shouldShow = computed(() => {
  return Boolean(trace.value || steps.value.length);
});

const title = computed(() => {
  if (chunks.value.length || hasRetrievalMeta.value) return '检索过程';
  return '搜寻记录';
});

const modeLabel = computed(() => {
  if (chunks.value.length || hasRetrievalMeta.value) return 'RAG';
  if (toolName.value || steps.value.length) return '本地工具';
  return '占位';
});

const summaryText = computed(() => {
  const parts = [];
  if (toolName.value) parts.push(toolName.value);
  if (steps.value.length) parts.push(`${steps.value.length} 步`);
  if (chunks.value.length) parts.push(`${chunks.value.length} 个片段`);
  return parts.length ? parts.join(' · ') : '未启用 RAG';
});
</script>
