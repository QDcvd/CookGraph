<template>
  <div class="app-wrapper">
    <!-- Sidebar (Left) -->
    <Sidebar />

    <!-- Main Content Area -->
    <main class="main-content">
      <!-- History Sidebar (Slides in on right if enabled) -->
      <HistorySidebar />

      <!-- Chat Area -->
      <ChatArea />
    </main>
  </div>
</template>

<script setup lang="ts">
import Sidebar from '@/components/Sidebar.vue';
import HistorySidebar from '@/components/HistorySidebar.vue';
import ChatArea from '@/components/Chat/ChatArea.vue';
</script>
