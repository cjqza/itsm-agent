<template>
  <div class="ticket-list-page">
    <el-card>
      <template #header>
        <div class="toolbar">
          <div class="left">
            <el-button type="primary" @click="loadTickets">刷新</el-button>
          </div>
          <div class="right">
            <el-input v-model="keyword" placeholder="搜索工单号/标题" style="width:200px" @keyup.enter="loadTickets" clearable>
              <template #append><el-button @click="loadTickets"><el-icon><Search /></el-icon></el-button></template>
            </el-input>
            <el-select v-model="filters.status" clearable placeholder="状态" style="width:120px" @change="loadTickets">
              <el-option label="待接单" value="pending" />
              <el-option label="已接单" value="accepted" />
              <el-option label="处理中" value="processing" />
              <el-option label="待评价" value="resolved_pending_review" />
              <el-option label="已解决" value="resolved" />
            </el-select>
          </div>
        </div>
      </template>

      <el-skeleton :loading="loading" animated :rows="6">
        <template #template>
          <div style="padding: 12px 0;">
            <el-skeleton-item variant="text" style="width: 100%; height: 40px; margin-bottom: 8px;" v-for="i in 6" :key="i" />
          </div>
        </template>
        <template #default>
          <el-table :data="tickets" stripe @row-click="goToDetail">
            <el-table-column width="50">
              <template #default="{ row }">
                <div class="sla-indicator" :style="{ background: slaColor(row.sla_status) }"></div>
              </template>
            </el-table-column>
            <el-table-column prop="ticket_no" label="工单号" width="130">
              <template #default="{ row }">
                <span class="ticket-no">{{ row.ticket_no }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
            <el-table-column prop="category_name" label="业务系统" width="100">
              <template #default="{ row }">{{ row.category_name || '-' }}</template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="100">
              <template #default="{ row }">
                <el-tag :type="statusTagType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="creator_name" label="提交人" width="80" />
            <el-table-column prop="assignee_name" label="处理人" width="80">
              <template #default="{ row }">{{ row.assignee_name || '-' }}</template>
            </el-table-column>
            <el-table-column label="闭环" width="60">
              <template #default="{ row }">
                <el-tag :type="row.status === 'resolved' ? 'success' : 'danger'" size="small" effect="dark">
                  {{ row.status === 'resolved' ? '已闭环' : '待处理' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="提交时间" width="140">
              <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
            </el-table-column>
          </el-table>
        </template>
      </el-skeleton>

      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        layout="total, prev, pager, next"
        style="margin-top:16px; justify-content:flex-end"
        @current-change="loadTickets"
      />
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, watch, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/store/user'
import { ticketApi } from '@/api'
import { slaColor, statusTagType, statusText } from '@shared/utils/status'
import { formatTime } from '@shared/utils/format'

const router = useRouter()
const store = useUserStore()
const tickets = ref([])
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const keyword = ref('')
const filters = reactive({ status: '' })

// 搜索防抖（300ms）
let searchTimer = null
watch(keyword, () => {
  clearTimeout(searchTimer)
  page.value = 1
  searchTimer = setTimeout(() => loadTickets(), 300)
})

onMounted(() => loadTickets())

// 监听全局WebSocket通知，自动刷新列表
let unsubWs = null
onMounted(() => {
  unsubWs = store.onWsMessage((msg) => {
    if (msg.type === 'new_ticket' || msg.type === 'ticket_update') {
      loadTickets()
    }
  })
})
onUnmounted(() => { if (unsubWs) unsubWs() })

async function loadTickets() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (filters.status) params.status = filters.status
    if (keyword.value) params.keyword = keyword.value
    const data = await ticketApi.list(params)
    tickets.value = data.items || []
    total.value = data.total || 0
  } finally { loading.value = false }
}

function goToDetail(row) { router.push(`/tickets/${row.id}`) }
</script>

<style scoped>
.ticket-list-page :deep(.el-card) {
  border-radius: 12px;
  border: none;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04), 0 1px 2px rgba(0, 0, 0, 0.06);
}

.ticket-list-page :deep(.el-card__header) {
  padding: 16px 20px;
  border-bottom: 1px solid #f0f0f0;
  background: #fafbfc;
  border-radius: 12px 12px 0 0;
}

.toolbar { display: flex; justify-content: space-between; align-items: center; }
.right { display: flex; gap: 8px; }

.sla-indicator { width: 4px; height: 28px; border-radius: 2px; }
.ticket-no { color: #2563eb; font-weight: 600; font-size: 13px; }

/* 表格行 hover 效果 */
.ticket-list-page :deep(.el-table) {
  border-radius: 8px;
  overflow: hidden;
}
.ticket-list-page :deep(.el-table th.el-table__cell) {
  background: #f8fafc;
  font-weight: 600;
  color: #475569;
  font-size: 13px;
}
.ticket-list-page :deep(.el-table .el-table__row) {
  cursor: pointer;
  transition: background 0.15s;
}
.ticket-list-page :deep(.el-table .el-table__row:hover > td) {
  background: #f0f7ff !important;
}

/* 空状态 */
.ticket-list-page :deep(.el-table__empty-block) {
  min-height: 200px;
}
</style>
