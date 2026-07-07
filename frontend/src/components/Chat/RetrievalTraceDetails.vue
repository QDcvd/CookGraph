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

        <div v-if="hybridRetrieval" class="process-hybrid">
          <div class="process-section-title">混合召回</div>
          <div class="process-grid">
            <div>
              <b>策略</b>
              <span>{{ hybridRetrieval.strategy || 'alias + lexical + dense + rrf' }}</span>
            </div>
            <div v-if="hybridStatus">
              <b>状态</b>
              <span>{{ hybridStatus }}</span>
            </div>
            <div v-if="hybridRetrieval.standard_dish">
              <b>标准菜名</b>
              <span>{{ hybridRetrieval.standard_dish }}</span>
            </div>
            <div v-if="hybridRetrieval.rewritten_query">
              <b>改写查询</b>
              <span>{{ hybridRetrieval.rewritten_query }}</span>
            </div>
            <div v-if="hybridRetrieval.score !== undefined && hybridRetrieval.score !== null">
              <b>Score</b>
              <span>{{ formatScore(hybridRetrieval.score) }}</span>
            </div>
            <div v-if="hybridRetrieval.margin !== undefined && hybridRetrieval.margin !== null">
              <b>Margin</b>
              <span>{{ formatScore(hybridRetrieval.margin) }}</span>
            </div>
          </div>

          <div v-if="hybridCandidates.length" class="process-candidates">
            <b>候选</b>
            <span v-for="candidate in hybridCandidates" :key="candidate.name" class="process-candidate">
              {{ candidate.name }}<small v-if="candidate.score !== undefined && candidate.score !== null">{{ formatScore(candidate.score) }}</small>
            </span>
          </div>

          <div v-if="hybridDebug.length" class="process-debug">
            <div v-for="item in hybridDebug" :key="item.label">
              <b>{{ item.label }}</b>
              <span>{{ item.value }}</span>
            </div>
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
const hybridRetrieval = computed(() => trace.value?.hybrid_retrieval || null);
const hybridCandidates = computed(() => hybridRetrieval.value?.candidates || []);
const hybridDebug = computed(() => {
  const value = hybridRetrieval.value;
  if (!value) return [];
  return [
    { label: 'alias', value: value.alias_debug },
    { label: 'lexical', value: value.lexical_debug },
    { label: 'dense', value: value.dense_debug },
  ].filter((item): item is { label: string; value: string } => Boolean(item.value));
});
const hybridStatus = computed(() => {
  const value = hybridRetrieval.value;
  if (!value) return '';
  if (value.skipped) return '跳过';
  if (value.accepted) return '已改写';
  if (value.not_rewritten) return '未改写';
  return '已召回';
});

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
    value.hybrid_retrieval ||
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
  if (hybridRetrieval.value?.standard_dish) parts.push(`标准菜名：${hybridRetrieval.value.standard_dish}`);
  if (steps.value.length) parts.push(`${steps.value.length} 步`);
  if (chunks.value.length) parts.push(`${chunks.value.length} 个片段`);
  return parts.length ? parts.join(' · ') : '未启用 RAG';
});

const formatScore = (value: number) => {
  if (!Number.isFinite(value)) return String(value);
  return value.toFixed(3);
};
</script>
