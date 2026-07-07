<template>
  <div v-if="usage" class="token-usage-badge">
    <span class="token-usage-item">
      <i class="fas fa-gauge-high"></i>
      <span>输出 tokens：{{ usage.completion_tokens_estimated }}</span>
      <span class="token-usage-source">估算</span>
    </span>
    <span v-if="hasProviderUsage" class="token-usage-item">
      <i class="fas fa-coins"></i>
      <span>总 tokens：{{ usage.total_tokens }}</span>
      <span class="token-usage-source">模型返回</span>
    </span>
    <span v-if="usage.input_tokens !== null && usage.input_tokens !== undefined" class="token-usage-mini">
      输入 {{ usage.input_tokens }}
    </span>
    <span v-if="usage.output_tokens !== null && usage.output_tokens !== undefined" class="token-usage-mini">
      输出 {{ usage.output_tokens }}
    </span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import type { TokenUsage } from '@/types/chat';

const props = defineProps<{
  usage?: TokenUsage | null;
}>();

const hasProviderUsage = computed(() => (
  props.usage?.total_tokens !== null && props.usage?.total_tokens !== undefined
));
</script>
