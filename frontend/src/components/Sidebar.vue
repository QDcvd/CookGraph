<template>
  <aside class="sidebar">
    <div class="sidebar-header">
      <div class="logo-icon">
        <i class="fas fa-utensils"></i>
      </div>
      <div>
        <h2>迷你烹饪</h2>
        <p class="sidebar-subtitle">Cooking QA Bot</p>
      </div>
    </div>
    <nav class="sidebar-nav">
      <button @click="onNewChat" :class="['nav-btn', { active: chatStore.activeNav === 'newChat' }]">
        <i class="fas fa-magnifying-glass"></i> 开始提问
      </button>
      <button @click="onHistory" :class="['nav-btn', { active: chatStore.activeNav === 'history' }]">
        <i class="fas fa-clock-rotate-left"></i> 问答记录
      </button>
      <!-- 设置按钮已隐藏（Phase 1 不包含 RAG/文档管理） -->
    </nav>
    <div class="sidebar-footer">
      <div class="cook-card">
        <span class="cook-label">今日状态</span>
        <strong>菜谱资料待查</strong>
        <small>把菜名、食材、菜单文件或项目路径发来。</small>
      </div>
      <button @click="chatStore.handleClearChat" class="danger-btn">
        <i class="fas fa-broom"></i> 清空对话
      </button>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { useChatStore } from '@/stores/chat';
import { useSessionStore } from '@/stores/sessions';

const chatStore = useChatStore();
const sessionStore = useSessionStore();

const onNewChat = () => {
  chatStore.handleNewChat();
};

const onHistory = async () => {
  chatStore.activeNav = 'history';
  sessionStore.showHistorySidebar = !sessionStore.showHistorySidebar;
  if (sessionStore.showHistorySidebar) {
    try {
      await sessionStore.fetchSessions();
    } catch (error: any) {
      alert(error.message);
    }
  }
};
</script>
