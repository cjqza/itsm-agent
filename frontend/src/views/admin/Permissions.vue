<template>
  <div class="permissions">
    <el-tabs v-model="activeTab" class="perm-tabs">
      <!-- 账号管理 -->
      <el-tab-pane label="账号管理" name="list">
        <el-card class="table-card">
          <template #header>
            <div class="card-header-bar">
              <span class="card-title">账号管理</span>
              <div class="search-bar">
                <el-select
                  v-model="roleFilter"
                  placeholder="角色筛选"
                  clearable
                  style="width: 140px"
                  size="small"
                  @change="handleRoleFilter"
                >
                  <el-option label="超级管理员" value="super_admin" />
                  <el-option label="管理员" value="admin" />
                  <el-option label="客服" value="agent" />
                  <el-option label="普通用户" value="user" />
                  <el-option label="已锁定" value="locked" />
                </el-select>
                <el-input
                  v-model="searchKeyword"
                  placeholder="搜索姓名、账号、手机号、邮箱"
                  clearable
                  style="width: 260px"
                  size="small"
                  @input="handleSearchDebounced"
                  @keyup.enter="handleSearch"
                  @clear="handleSearchClear"
                >
                  <template #prefix><el-icon><Search /></el-icon></template>
                </el-input>
                <el-button type="primary" size="small" @click="handleSearch">搜索</el-button>
                <el-button size="small" @click="handleReset">重置</el-button>
                <el-button type="success" size="small" @click="openAddDialog">新增客服</el-button>
                <el-button v-if="userStore.isSuperAdmin" type="warning" size="small" @click="openAdminDialog">新增管理员</el-button>
              </div>
            </div>
          </template>
          <el-table :data="users" stripe v-loading="loading" class="perm-table" empty-text="暂无用户数据">
            <el-table-column prop="login_id" label="账号" width="100" />
            <el-table-column prop="name" label="姓名" width="100" />
            <el-table-column prop="role" label="角色" width="100">
              <template #default="{ row }">
                <el-tag :type="row.role === 'admin' || row.role === 'super_admin' ? 'danger' : row.role === 'agent' ? 'warning' : 'info'" size="small" effect="light" round>
                  {{ roleText(row.role) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="phone" label="手机号" width="120" />
            <el-table-column prop="email" label="邮箱" min-width="150" show-overflow-tooltip />
            <el-table-column label="ITSM" width="60" align="center">
              <template #default="{ row }">
                <el-icon v-if="row.itsm_access" color="#67c23a"><SuccessFilled /></el-icon>
                <el-icon v-else color="#c0c4cc"><CircleCloseFilled /></el-icon>
              </template>
            </el-table-column>
            <el-table-column label="OPS" width="60" align="center">
              <template #default="{ row }">
                <el-icon v-if="row.ops_access" color="#67c23a"><SuccessFilled /></el-icon>
                <el-icon v-else color="#c0c4cc"><CircleCloseFilled /></el-icon>
              </template>
            </el-table-column>
            <el-table-column label="后台" width="60" align="center">
              <template #default="{ row }">
                <el-icon v-if="row.admin_access" color="#67c23a"><SuccessFilled /></el-icon>
                <el-icon v-else color="#c0c4cc"><CircleCloseFilled /></el-icon>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="80" align="center">
              <template #default="{ row }">
                <el-tag :type="row.locked_until ? 'danger' : row.status === 'active' ? 'success' : 'info'" size="small" effect="light" round>
                  {{ row.locked_until ? '已锁定' : row.status === 'active' ? '正常' : '已禁用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="150" align="center">
              <template #default="{ row }">
                <el-button type="primary" size="small" link @click="openSettingsDialog(row)">设置</el-button>
                <el-button
                  v-if="row.locked_until"
                  type="warning"
                  size="small"
                  link
                  @click="handleUnlock(row)"
                >解锁</el-button>
              </template>
            </el-table-column>
          </el-table>
          <!-- 分页 -->
          <div class="pagination-bar" v-if="total > pageSize">
            <el-pagination
              v-model:current-page="currentPage"
              :page-size="pageSize"
              :total="total"
              layout="total, prev, pager, next"
              small
              @current-change="handlePageChange"
            />
          </div>
        </el-card>
      </el-tab-pane>

      <!-- 权限申请（保留原样） -->
      <el-tab-pane label="权限申请" name="requests">
        <el-card class="table-card">
          <template #header>
            <div class="card-header-bar">
              <span class="card-title">权限申请列表</span>
              <el-select v-model="reqStatusFilter" clearable placeholder="状态筛选" style="width: 120px" size="small" @change="loadRequests">
                <el-option label="待审批" value="pending" />
                <el-option label="已批准" value="approved" />
                <el-option label="已拒绝" value="rejected" />
              </el-select>
            </div>
          </template>
          <el-table :data="requests" stripe v-loading="loadingReq" class="perm-table" empty-text="暂无申请记录">
            <el-table-column prop="id" label="ID" width="60" />
            <el-table-column prop="user_name" label="申请人" width="100">
              <template #default="{ row }">
                <span class="user-name-cell">{{ row.user_name }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="request_type" label="申请权限" width="120">
              <template #default="{ row }">
                <el-tag size="small" effect="light">{{ typeText(row.request_type) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="reason" label="申请理由" min-width="150" show-overflow-tooltip />
            <el-table-column prop="status" label="状态" width="100">
              <template #default="{ row }">
                <el-tag :type="row.status === 'pending' ? 'warning' : row.status === 'approved' ? 'success' : 'danger'" size="small" effect="light" round>
                  {{ statusText(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="申请时间" width="160" />
            <el-table-column label="操作" width="160" v-if="reqStatusFilter !== 'approved' && reqStatusFilter !== 'rejected'">
              <template #default="{ row }">
                <template v-if="row.status === 'pending'">
                  <el-button type="success" size="small" @click="reviewReq(row.id, 'approved')" plain>批准</el-button>
                  <el-button type="danger" size="small" @click="reviewReq(row.id, 'rejected')" plain>拒绝</el-button>
                </template>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 用户设置对话框 -->
    <el-dialog
      v-model="settingsDialogVisible"
      title="用户设置"
      width="560px"
      :close-on-click-modal="false"
      @closed="resetSettingsForm"
    >
      <template v-if="settingsUser">
        <el-tabs v-model="settingsTab">
          <!-- 基本信息 -->
          <el-tab-pane label="基本信息" name="info">
            <el-form :model="settingsForm" label-width="80px" label-position="right">
              <el-form-item label="账号">
                <el-input :value="settingsUser.login_id" disabled />
              </el-form-item>
              <el-form-item label="姓名">
                <el-input v-model="settingsForm.name" placeholder="请输入姓名" />
              </el-form-item>
              <el-form-item label="手机号">
                <el-input v-model="settingsForm.phone" placeholder="请输入手机号" />
              </el-form-item>
              <el-form-item label="邮箱">
                <el-input v-model="settingsForm.email" placeholder="请输入邮箱" />
              </el-form-item>
              <el-form-item label="部门">
                <el-input v-model="settingsForm.department" placeholder="请输入部门" />
              </el-form-item>
              <el-form-item label="角色">
                <el-tag :type="settingsUser.role === 'admin' || settingsUser.role === 'super_admin' ? 'danger' : settingsUser.role === 'agent' ? 'warning' : 'info'" size="small">
                  {{ roleText(settingsUser.role) }}
                </el-tag>
              </el-form-item>
              <el-form-item label="状态">
                <el-tag :type="settingsUser.status === 'active' ? 'success' : 'info'" size="small">
                  {{ settingsUser.status === 'active' ? '正常' : '已禁用' }}
                </el-tag>
                <el-tag v-if="settingsUser.locked_until" type="danger" size="small" style="margin-left: 8px;">
                  已锁定
                </el-tag>
              </el-form-item>
            </el-form>
          </el-tab-pane>

          <!-- 权限管理 -->
          <el-tab-pane label="权限管理" name="perms">
            <div class="perm-settings">
              <div class="perm-item">
                <div class="perm-label">ITSM 权限</div>
                <div class="perm-desc">工单管理、接单、处理、转派</div>
                <el-switch
                  v-model="settingsPermForm.itsm_access"
                  :disabled="!canEditPerms"
                  @change="handlePermChange('itsm')"
                />
              </div>
              <div class="perm-item">
                <div class="perm-label">OPS 权限</div>
                <div class="perm-desc">数据统计、报表导出、绩效分析</div>
                <el-switch
                  v-model="settingsPermForm.ops_access"
                  :disabled="!canEditPerms"
                  @change="handlePermChange('ops')"
                />
              </div>
              <div class="perm-item">
                <div class="perm-label">后台管理权限</div>
                <div class="perm-desc">用户管理、权限配置、系统设置</div>
                <el-switch
                  v-model="settingsPermForm.admin_access"
                  :disabled="!userStore.isSuperAdmin"
                  @change="handlePermChange('admin')"
                />
                <div v-if="!userStore.isSuperAdmin" class="perm-hint">
                  仅超级管理员可修改后台权限
                </div>
              </div>

              <div v-if="settingsUser.role === 'agent'" style="margin-top: 20px; padding-top: 16px; border-top: 1px solid #eee;">
                <el-button type="danger" size="small" @click="handleDowngradeFromSettings">
                  取消客服身份
                </el-button>
              </div>
              <div v-else-if="settingsUser.role === 'user'" style="margin-top: 20px; padding-top: 16px; border-top: 1px solid #eee;">
                <el-button type="success" size="small" @click="handleUpgradeFromSettings">
                  升级为客服
                </el-button>
              </div>
            </div>
          </el-tab-pane>
        </el-tabs>
      </template>
      <template #footer>
        <el-button @click="settingsDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleSaveUserInfo">保存</el-button>
      </template>
    </el-dialog>

    <!-- 客服管理对话框 -->
    <el-dialog
      v-model="dialogVisible"
      title="客服管理"
      width="560px"
      :close-on-click-modal="false"
      @closed="resetForm"
    >
      <el-tabs v-model="agentDialogTab">
        <!-- 升级为客服 -->
        <el-tab-pane label="升级为客服" name="upgrade">
          <div style="margin-bottom: 12px;">
            <el-input
              v-model="upgradeSearch"
              placeholder="搜索用户姓名、账号、手机号"
              clearable
              @input="searchUsersForUpgrade"
            >
              <template #prefix><el-icon><Search /></el-icon></template>
            </el-input>
          </div>
          <el-table
            ref="upgradeTableRef"
            :data="upgradeUsers"
            max-height="280"
            @selection-change="handleUpgradeSelectionChange"
            style="width: 100%"
            v-loading="upgradeLoading"
          >
            <el-table-column type="selection" width="50" />
            <el-table-column prop="login_id" label="账号" width="100" />
            <el-table-column prop="name" label="姓名" width="100" />
            <el-table-column prop="phone" label="手机号" width="130" />
            <el-table-column prop="role" label="角色" width="80">
              <template #default="{ row }">
                <el-tag size="small">{{ roleText(row.role) }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
          <div v-if="selectedUpgradeUsers.length > 0" style="margin-top: 12px; padding: 8px; background: #f0f9ff; border-radius: 4px; font-size: 13px;">
            已选择 <strong>{{ selectedUpgradeUsers.length }}</strong> 个用户：
            <span v-for="(u, idx) in selectedUpgradeUsers" :key="u.id">
              {{ u.name }}（{{ u.login_id }}）{{ idx < selectedUpgradeUsers.length - 1 ? '、' : '' }}
            </span>
          </div>
          <div style="text-align: right; margin-top: 12px;">
            <el-button
              type="primary"
              size="small"
              :loading="submitting"
              :disabled="selectedUpgradeUsers.length === 0"
              @click="handleBatchUpgrade"
            >升级为客服（{{ selectedUpgradeUsers.length }}）</el-button>
          </div>
        </el-tab-pane>

        <!-- 取消客服 -->
        <el-tab-pane label="取消客服" name="downgrade">
          <div style="margin-bottom: 12px;">
            <el-input
              v-model="downgradeSearch"
              placeholder="搜索客服姓名、账号、手机号"
              clearable
              @input="searchAgentsForDowngrade"
            >
              <template #prefix><el-icon><Search /></el-icon></template>
            </el-input>
          </div>
          <el-table
            ref="downgradeTableRef"
            :data="downgradeAgents"
            max-height="280"
            @selection-change="handleDowngradeSelectionChange"
            style="width: 100%"
            v-loading="downgradeLoading"
          >
            <el-table-column type="selection" width="50" />
            <el-table-column prop="login_id" label="账号" width="100" />
            <el-table-column prop="name" label="姓名" width="100" />
            <el-table-column prop="phone" label="手机号" width="130" />
            <el-table-column prop="status" label="状态" width="80">
              <template #default="{ row }">
                <el-tag :type="row.status === 'active' ? 'success' : 'info'" size="small">
                  {{ row.status === 'active' ? '正常' : '已禁用' }}
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
          <div v-if="selectedDowngradeAgents.length > 0" style="margin-top: 12px; padding: 8px; background: #fef2f2; border-radius: 4px; font-size: 13px;">
            已选择 <strong>{{ selectedDowngradeAgents.length }}</strong> 个客服：
            <span v-for="(u, idx) in selectedDowngradeAgents" :key="u.id">
              {{ u.name }}（{{ u.login_id }}）{{ idx < selectedDowngradeAgents.length - 1 ? '、' : '' }}
            </span>
          </div>
          <div style="text-align: right; margin-top: 12px;">
            <el-button
              type="danger"
              size="small"
              :loading="submitting"
              :disabled="selectedDowngradeAgents.length === 0"
              @click="handleBatchDowngrade"
            >取消客服（{{ selectedDowngradeAgents.length }}）</el-button>
          </div>
        </el-tab-pane>
      </el-tabs>
      <template #footer>
        <el-button @click="dialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 设置管理员对话框 -->
    <el-dialog
      v-model="adminDialogVisible"
      title="设置管理员"
      width="500px"
      :close-on-click-modal="false"
      @closed="resetAdminForm"
    >
      <div style="margin-bottom: 16px;">
        <div style="font-size: 13px; color: #666; margin-bottom: 8px;">
          输入用户的账号或姓名进行匹配，确认后设置为管理员。
        </div>
        <div style="display: flex; gap: 8px;">
          <el-input
            v-model="adminSearchKeyword"
            placeholder="输入账号或姓名搜索"
            clearable
            @input="searchUserForAdmin"
            style="flex: 1"
          >
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
          <el-select v-model="adminForm.role" placeholder="角色" style="width: 140px">
            <el-option label="管理员" value="admin" />
            <el-option label="超级管理员" value="super_admin" />
          </el-select>
        </div>
      </div>

      <div v-if="adminSearchLoading" style="text-align: center; padding: 20px;">
        <el-icon class="is-loading"><Loading /></el-icon> 搜索中...
      </div>

      <el-table
        v-else-if="adminSearchResults.length > 0"
        :data="adminSearchResults"
        max-height="250"
        highlight-current-row
        @current-change="handleAdminUserSelect"
        style="width: 100%"
      >
        <el-table-column prop="login_id" label="账号" width="100" />
        <el-table-column prop="name" label="姓名" width="100" />
        <el-table-column prop="phone" label="手机号" width="130" />
        <el-table-column prop="role" label="当前角色" width="80">
          <template #default="{ row }">
            <el-tag size="small">{{ roleText(row.role) }}</el-tag>
          </template>
        </el-table-column>
      </el-table>

      <div v-else-if="adminSearchKeyword && !adminSearchLoading" style="text-align: center; padding: 20px; color: #999;">
        未找到匹配用户
      </div>

      <div v-if="selectedAdminUser" style="margin-top: 12px; padding: 10px; background: #f0f9ff; border-radius: 6px; font-size: 13px;">
        已选择：<strong>{{ selectedAdminUser.name }}</strong>（{{ selectedAdminUser.login_id }}）
        → 将设置为 <el-tag size="small" type="danger">{{ adminForm.role === 'super_admin' ? '超级管理员' : '管理员' }}</el-tag>
      </div>

      <template #footer>
        <el-button @click="adminDialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="submitting"
          :disabled="!selectedAdminUser"
          @click="handleSetAdmin"
        >确认设置</el-button>
      </template>
    </el-dialog>

    <!-- 编辑管理员对话框 -->
    <el-dialog
      v-model="editAdminDialogVisible"
      title="编辑管理员"
      width="480px"
      :close-on-click-modal="false"
      @closed="resetEditAdminForm"
    >
      <el-form
        ref="editAdminFormRef"
        :model="editAdminForm"
        :rules="editAdminFormRules"
        label-width="80px"
        label-position="right"
      >
        <el-form-item label="姓名" prop="name">
          <el-input v-model="editAdminForm.name" placeholder="请输入姓名" />
        </el-form-item>
        <el-form-item label="手机号" prop="phone">
          <el-input v-model="editAdminForm.phone" placeholder="请输入手机号" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="editAdminForm.email" placeholder="请输入邮箱（可选）" />
        </el-form-item>
        <el-form-item label="部门">
          <el-input v-model="editAdminForm.department" placeholder="请输入部门（可选）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editAdminDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleEditAdminSubmit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, SuccessFilled, CircleCloseFilled } from '@element-plus/icons-vue'
import { adminApi } from '@/api'
import { useUserStore } from '@/store/user'

const userStore = useUserStore()
const isSuperAdmin = userStore.isSuperAdmin

// ===== 账号管理 =====
const activeTab = ref('list')
const users = ref([])
const loading = ref(false)
const searchKeyword = ref('')
const roleFilter = ref('')
const currentPage = ref(1)
const pageSize = ref(20)
const total = ref(0)

// ===== 权限申请 =====
const requests = ref([])
const loadingReq = ref(false)
const reqStatusFilter = ref('pending')

// ===== 对话框 =====
const dialogVisible = ref(false)
const detailDialogVisible = ref(false)
const detailUser = ref(null)
const settingsDialogVisible = ref(false)
const settingsUser = ref(null)
const settingsTab = ref('info')
const settingsForm = reactive({
  name: '',
  phone: '',
  email: '',
  department: '',
})
const settingsPermForm = reactive({
  itsm_access: false,
  ops_access: false,
  admin_access: false,
})
const submitting = ref(false)
const formRef = ref(null)
const editingUserId = ref(null)
const isEditMode = ref(false)

// 是否有权限编辑（admin 或 super_admin）
const canEditPerms = computed(() => userStore.isAdmin)

const agentForm = reactive({
  name: '',
  phone: '',
  password: '',
  email: '',
  department: '',
})

const formRules = {
  name: [{ required: true, message: '请输入姓名', trigger: 'blur' }],
  phone: [{ required: true, message: '请输入手机号', trigger: 'blur' }],
}

// ===== 升级为客服 =====
const upgradeUsers = ref([])
const upgradeLoading = ref(false)
const upgradeSearch = ref('')
const selectedUpgradeUsers = ref([])
const upgradeTableRef = ref(null)
const agentDialogTab = ref('upgrade')

// 取消客服相关
const downgradeAgents = ref([])
const downgradeLoading = ref(false)
const downgradeSearch = ref('')
const selectedDowngradeAgents = ref([])
const downgradeTableRef = ref(null)

async function searchUsersForUpgrade() {
  upgradeLoading.value = true
  try {
    const params = { role: 'user', page_size: 50 }
    if (upgradeSearch.value.trim()) {
      params.keyword = upgradeSearch.value.trim()
    }
    const res = await adminApi.getUsers(params)
    upgradeUsers.value = res?.items || []
  } catch (e) {
    ElMessage.error('加载用户列表失败')
    upgradeUsers.value = []
  } finally {
    upgradeLoading.value = false
  }
}

function handleUpgradeSelectionChange(selection) {
  selectedUpgradeUsers.value = selection
}

async function searchAgentsForDowngrade() {
  downgradeLoading.value = true
  try {
    const params = { role: 'agent', page_size: 50 }
    if (downgradeSearch.value.trim()) {
      params.keyword = downgradeSearch.value.trim()
    }
    const res = await adminApi.getUsers(params)
    downgradeAgents.value = res?.items || []
  } catch (e) {
    ElMessage.error('加载客服列表失败')
    downgradeAgents.value = []
  } finally {
    downgradeLoading.value = false
  }
}

function handleDowngradeSelectionChange(selection) {
  selectedDowngradeAgents.value = selection
}

async function handleBatchDowngrade() {
  if (selectedDowngradeAgents.value.length === 0) {
    ElMessage.warning('请选择要取消的客服')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确定要取消 ${selectedDowngradeAgents.value.length} 个客服的权限吗？`,
      '操作确认',
      { confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }
  submitting.value = true
  const agents = [...selectedDowngradeAgents.value]
  let successCount = 0
  let failCount = 0
  try {
    for (const agent of agents) {
      try {
        await adminApi.downgradeToUser(agent.id)
        successCount++
      } catch (e) {
        failCount++
      }
    }
    if (successCount > 0) {
      ElMessage.success(`已取消 ${successCount} 个客服权限`)
    }
    if (failCount > 0) {
      ElMessage.warning(`${failCount} 个操作失败`)
    }
    dialogVisible.value = false
    await loadUsers()
  } catch (e) {
    ElMessage.error('批量操作失败')
  } finally {
    submitting.value = false
  }
}

async function handleBatchUpgrade() {
  if (selectedUpgradeUsers.value.length === 0) {
    ElMessage.warning('请选择要升级的用户')
    return
  }
  submitting.value = true
  const users = [...selectedUpgradeUsers.value]
  let successCount = 0
  let failCount = 0
  try {
    for (const user of users) {
      try {
        await adminApi.upgradeToAgent(user.id)
        successCount++
      } catch (e) {
        failCount++
      }
    }
    if (successCount > 0) {
      ElMessage.success(`已成功升级 ${successCount} 个用户为客服`)
    }
    if (failCount > 0) {
      ElMessage.warning(`${failCount} 个用户升级失败（可能已是客服）`)
    }
    dialogVisible.value = false
    await loadUsers()
  } catch (e) {
    ElMessage.error('批量升级失败')
  } finally {
    submitting.value = false
  }
}

async function handleDowngrade(row) {
  try {
    await ElMessageBox.confirm(
      `确定要取消「${row.name}」的客服权限吗？取消后将变为普通用户。`,
      '操作确认',
      { confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning' }
    )
    await adminApi.downgradeToUser(row.id)
    ElMessage.success(`已取消 ${row.name} 的客服权限`)
    detailDialogVisible.value = false
    await loadUsers()
  } catch (e) {
    if (e !== 'cancel') {
      // error handled by interceptor
    }
  }
}

// ===== 设置管理员 =====
const adminDialogVisible = ref(false)
const adminSearchKeyword = ref('')
const adminSearchResults = ref([])
const adminSearchLoading = ref(false)
const selectedAdminUser = ref(null)
const adminForm = reactive({ role: 'admin' })

async function searchUserForAdmin() {
  if (!adminSearchKeyword.value.trim()) {
    adminSearchResults.value = []
    return
  }
  adminSearchLoading.value = true
  try {
    const res = await adminApi.getUsers({ keyword: adminSearchKeyword.value.trim(), page_size: 20 })
    // 过滤掉已经是管理员/超级管理员的用户
    adminSearchResults.value = (res?.items || []).filter(u => u.role !== 'admin' && u.role !== 'super_admin')
  } catch (e) {
    ElMessage.error('搜索失败')
    adminSearchResults.value = []
  } finally {
    adminSearchLoading.value = false
  }
}

function handleAdminUserSelect(row) {
  selectedAdminUser.value = row
}

// ===== 编辑管理员 =====
const editAdminDialogVisible = ref(false)
const editAdminFormRef = ref(null)
const editingAdminId = ref(null)
const editAdminForm = reactive({
  name: '',
  phone: '',
  email: '',
  department: '',
})
const editAdminFormRules = {
  name: [{ required: true, message: '请输入姓名', trigger: 'blur' }],
  phone: [{ required: true, message: '请输入手机号', trigger: 'blur' }],
}

onMounted(async () => {
  await Promise.all([loadUsers(), loadRequests()])
})

// ===== 账号管理方法 =====

async function loadUsers() {
  loading.value = true
  try {
    const params = {
      page: currentPage.value,
      page_size: pageSize.value,
    }
    if (searchKeyword.value.trim()) {
      params.keyword = searchKeyword.value.trim()
    }
    if (roleFilter.value === 'locked') {
      params.locked = true
    } else if (roleFilter.value) {
      params.role = roleFilter.value
    }
    const res = await adminApi.getUsers(params)
    users.value = res?.items || []
    total.value = res?.total || 0
  } catch (e) {
    ElMessage.error('加载用户列表失败')
    users.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

// 搜索防抖（300ms）
let _searchTimer = null
function handleSearchDebounced() {
  clearTimeout(_searchTimer)
  _searchTimer = setTimeout(() => handleSearch(), 300)
}

function handleSearch() {
  // 搜索时清除角色筛选，以便搜索到所有匹配用户（包括普通用户）
  roleFilter.value = ''
  currentPage.value = 1
  loadUsers()
}

function handleSearchClear() {
  searchKeyword.value = ''
  currentPage.value = 1
  loadUsers()
}

function handleRoleFilter() {
  searchKeyword.value = ''
  currentPage.value = 1
  loadUsers()
}

function handleReset() {
  searchKeyword.value = ''
  roleFilter.value = ''
  currentPage.value = 1
  loadUsers()
}

function handlePageChange(page) {
  currentPage.value = page
  loadUsers()
}

async function openAddDialog() {
  try {
    isEditMode.value = false
    editingUserId.value = null
    resetFormData()
    agentDialogTab.value = 'upgrade'
    upgradeSearch.value = ''
    selectedUpgradeUsers.value = []
    upgradeUsers.value = []
    downgradeSearch.value = ''
    selectedDowngradeAgents.value = []
    downgradeAgents.value = []
    dialogVisible.value = true
    await Promise.all([searchUsersForUpgrade(), searchAgentsForDowngrade()])
  } catch (e) {
    ElMessage.error('打开对话框失败')
  }
}

function openDetailDialog(row) {
  detailUser.value = row
  detailDialogVisible.value = true
}

function openSettingsDialog(row) {
  settingsUser.value = row
  settingsForm.name = row.name || ''
  settingsForm.phone = row.phone || ''
  settingsForm.email = row.email || ''
  settingsForm.department = row.department || ''
  settingsPermForm.itsm_access = row.itsm_access || false
  settingsPermForm.ops_access = row.ops_access || false
  settingsPermForm.admin_access = row.admin_access || false
  settingsTab.value = 'info'
  settingsDialogVisible.value = true
}

function resetSettingsForm() {
  settingsUser.value = null
  settingsForm.name = ''
  settingsForm.phone = ''
  settingsForm.email = ''
  settingsForm.department = ''
  settingsPermForm.itsm_access = false
  settingsPermForm.ops_access = false
  settingsPermForm.admin_access = false
}

async function handleSaveUserInfo() {
  if (!settingsUser.value) return
  submitting.value = true
  try {
    await adminApi.updateUser(settingsUser.value.id, {
      name: settingsForm.name,
      phone: settingsForm.phone,
      email: settingsForm.email,
      department: settingsForm.department,
    })
    ElMessage.success('保存成功')
    settingsDialogVisible.value = false
    await loadUsers()
  } catch (e) {
    // error handled by interceptor
  } finally {
    submitting.value = false
  }
}

async function handlePermChange(permType) {
  if (!settingsUser.value) return
  const permName = permType === 'itsm' ? 'ITSM' : permType === 'ops' ? 'OPS' : '后台'
  const newValue = settingsPermForm[`${permType}_access`]
  try {
    const params = {}
    params[`${permType}_access`] = newValue
    await adminApi.updatePermission(settingsUser.value.id, params)
    ElMessage.success(`已${newValue ? '开启' : '关闭'} ${permName} 权限`)
    await loadUsers()
    // 更新设置用户数据
    const updated = users.value.find(u => u.id === settingsUser.value.id)
    if (updated) settingsUser.value = updated
  } catch (e) {
    // 回滚 switch 状态
    settingsPermForm[`${permType}_access`] = !newValue
  }
}

async function handleUpgradeFromSettings() {
  if (!settingsUser.value) return
  try {
    await adminApi.upgradeToAgent(settingsUser.value.id)
    ElMessage.success(`已将 ${settingsUser.value.name} 升级为客服`)
    settingsDialogVisible.value = false
    await loadUsers()
  } catch (e) {
    // error handled by interceptor
  }
}

async function handleDowngradeFromSettings() {
  if (!settingsUser.value) return
  try {
    await ElMessageBox.confirm(
      `确定要取消「${settingsUser.value.name}」的客服身份吗？`,
      '操作确认',
      { confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning' }
    )
    await adminApi.downgradeToUser(settingsUser.value.id)
    ElMessage.success(`已取消 ${settingsUser.value.name} 的客服身份`)
    settingsDialogVisible.value = false
    await loadUsers()
  } catch (e) {
    if (e !== 'cancel') {
      // error handled by interceptor
    }
  }
}

async function handleRevokePermission(row, permType) {
  const permName = permType === 'itsm' ? 'ITSM' : 'OPS'
  try {
    await ElMessageBox.confirm(
      `确定要取消「${row.name}」的 ${permName} 权限吗？`,
      '操作确认',
      { confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning' }
    )
    const params = {}
    params[`${permType}_access`] = false
    await adminApi.updatePermission(row.id, params)
    ElMessage.success(`已取消 ${row.name} 的 ${permName} 权限`)
    await loadUsers()
    // 更新详情用户数据
    const updated = users.value.find(u => u.id === row.id)
    if (updated) detailUser.value = updated
  } catch (e) {
    if (e !== 'cancel') {
      // error handled by interceptor
    }
  }
}

function resetFormData() {
  agentForm.name = ''
  agentForm.phone = ''
  agentForm.password = ''
  agentForm.email = ''
  agentForm.department = ''
}

function resetForm() {
  formRef.value?.resetFields()
  resetFormData()
  editingUserId.value = null
}

async function handleSubmit() {
  if (formRef.value) {
    try {
      await formRef.value.validate()
    } catch {
      return
    }
  }

  submitting.value = true
  try {
    if (isEditMode.value) {
      const payload = {
        name: agentForm.name,
        phone: agentForm.phone,
        email: agentForm.email || null,
        department: agentForm.department || null,
      }
      await adminApi.updateAgent(editingUserId.value, payload)
      ElMessage.success('客服信息更新成功')
    } else {
      await adminApi.createAgent({
        name: agentForm.name,
        phone: agentForm.phone,
        password: agentForm.password,
        email: agentForm.email || null,
        department: agentForm.department || null,
      })
      ElMessage.success('客服创建成功')
    }
    dialogVisible.value = false
    await loadUsers()
  } catch (e) {
    // error already handled by interceptor
  } finally {
    submitting.value = false
  }
}

async function handleToggleStatus(row) {
  const isActive = row.status === 'active'
  const action = isActive ? '禁用' : '启用'
  try {
    await ElMessageBox.confirm(
      `确定要${action}客服「${row.name}」吗？`,
      '操作确认',
      { confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning' }
    )
    if (isActive) {
      await adminApi.deleteAgent(row.id)
    } else {
      // 重新启用：通过 updateAgent 将 status 改回 active
      // 但后端 updateAgent 不处理 status，所以直接用已有的 status 接口
      await adminApi.updateUserStatus(row.id, { status: 'active' })
    }
    ElMessage.success(`已${action}`)
    await loadUsers()
  } catch (e) {
    if (e !== 'cancel') {
      // error already handled by interceptor
    }
  }
}

async function handleUnlock(row) {
  try {
    await ElMessageBox.confirm(
      `确定要解锁账号「${row.name}」吗？`,
      '操作确认',
      { confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning' }
    )
    await adminApi.unlockUser(row.id)
    ElMessage.success('账号已解锁')
    await loadUsers()
  } catch (e) {
    if (e !== 'cancel') {
      // error already handled by interceptor
    }
  }
}

// ===== 管理员操作方法 =====

function openAdminDialog() {
  adminSearchKeyword.value = ''
  adminSearchResults.value = []
  selectedAdminUser.value = null
  adminForm.role = 'admin'
  adminDialogVisible.value = true
}

function resetAdminForm() {
  adminSearchKeyword.value = ''
  adminSearchResults.value = []
  selectedAdminUser.value = null
  adminForm.role = 'admin'
}

async function handleSetAdmin() {
  if (!selectedAdminUser.value) {
    ElMessage.warning('请先选择用户')
    return
  }
  const roleName = adminForm.role === 'super_admin' ? '超级管理员' : '管理员'
  try {
    await ElMessageBox.confirm(
      `确定要将「${selectedAdminUser.value.name}」（${selectedAdminUser.value.login_id}）设置为${roleName}吗？`,
      '操作确认',
      { confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }
  submitting.value = true
  try {
    await adminApi.createAdmin({
      name: selectedAdminUser.value.name,
      phone: selectedAdminUser.value.phone,
      login_id: selectedAdminUser.value.login_id,
      role: adminForm.role,
    })
    ElMessage.success(`已将 ${selectedAdminUser.value.name} 设置为${roleName}`)
    adminDialogVisible.value = false
    await loadUsers()
  } catch (e) {
    // error already handled by interceptor
  } finally {
    submitting.value = false
  }
}

function openEditAdminDialog(row) {
  editingAdminId.value = row.id
  editAdminForm.name = row.name || ''
  editAdminForm.phone = row.phone || ''
  editAdminForm.email = row.email || ''
  editAdminForm.department = row.department || ''
  editAdminDialogVisible.value = true
}

function resetEditAdminForm() {
  editAdminFormRef.value?.resetFields()
  editAdminForm.name = ''
  editAdminForm.phone = ''
  editAdminForm.email = ''
  editAdminForm.department = ''
  editingAdminId.value = null
}

async function handleEditAdminSubmit() {
  if (editAdminFormRef.value) {
    try {
      await editAdminFormRef.value.validate()
    } catch {
      return
    }
  }
  submitting.value = true
  try {
    const payload = {
      name: editAdminForm.name,
      phone: editAdminForm.phone,
      email: editAdminForm.email || null,
      department: editAdminForm.department || null,
    }
    await adminApi.updateUser(editingAdminId.value, payload)
    ElMessage.success('管理员信息更新成功')
    editAdminDialogVisible.value = false
    await loadUsers()
  } catch (e) {
    // error already handled by interceptor
  } finally {
    submitting.value = false
  }
}

async function handleToggleAdminStatus(row) {
  const isActive = row.status === 'active'
  const action = isActive ? '禁用' : '启用'
  try {
    await ElMessageBox.confirm(
      `确定要${action}管理员「${row.name}」吗？`,
      '操作确认',
      { confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning' }
    )
    await adminApi.updateUserStatus(row.id, { status: isActive ? 'inactive' : 'active' })
    ElMessage.success(`已${action}`)
    await loadUsers()
  } catch (e) {
    if (e !== 'cancel') {
      // error already handled by interceptor
    }
  }
}

// ===== 权限申请方法（保留原样） =====

async function loadRequests() {
  loadingReq.value = true
  try { requests.value = await adminApi.getPermissionRequests(reqStatusFilter.value) || [] } finally { loadingReq.value = false }
}

async function reviewReq(id, action) {
  try {
    await adminApi.reviewRequest(id, action)
    ElMessage.success(action === 'approved' ? '已批准' : '已拒绝')
    await loadRequests()
  } catch (e) { ElMessage.error('审批操作失败') }
}

// ===== 工具函数 =====

function roleText(r) { return { user: '普通用户', agent: '客服', admin: '管理员', super_admin: '超级管理员' }[r] || r }
function typeText(t) { return { itsm: 'ITSM系统', ops: 'OPS系统', admin: '后台管理' }[t] || t }
function statusText(s) { return { pending: '待审批', approved: '已批准', rejected: '已拒绝' }[s] || s }
</script>

<style scoped>
.permissions { }

.perm-tabs :deep(.el-tabs__item) {
  font-size: 14px;
  font-weight: 500;
}

.table-card {
  border-radius: 12px;
  border: none;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}
.table-card :deep(.el-card__header) {
  padding: 16px 20px;
  border-bottom: 1px solid #f0f0f0;
  background: #fafbfc;
  border-radius: 12px 12px 0 0;
}

.card-title { font-weight: 600; font-size: 14px; color: #1e293b; }
.card-header-bar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
.search-bar { display: flex; align-items: center; gap: 8px; }

.user-name-cell { font-weight: 500; color: #1e293b; }
.no-action { color: #c0c4cc; }

/* 表格样式 */
.perm-table { border-radius: 8px; overflow: hidden; }
.perm-table :deep(.el-table th.el-table__cell) {
  background: #f8fafc;
  font-weight: 600;
  color: #475569;
  font-size: 13px;
}
.perm-table :deep(.el-table .el-table__row:hover > td) {
  background: #f8fafc !important;
}

.pagination-bar {
  display: flex;
  justify-content: flex-end;
  padding: 16px 0 4px;
}

/* 权限设置样式 */
.perm-settings {
  padding: 8px 0;
}
.perm-item {
  display: flex;
  align-items: center;
  padding: 16px;
  margin-bottom: 12px;
  background: #f8fafc;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
}
.perm-label {
  font-weight: 600;
  color: #1e293b;
  min-width: 100px;
}
.perm-desc {
  flex: 1;
  color: #64748b;
  font-size: 13px;
  margin: 0 16px;
}
.perm-hint {
  color: #f56c6c;
  font-size: 12px;
  margin-left: 12px;
}
</style>
