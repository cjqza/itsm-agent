<template>
  <div class="home">
    <!-- 问题描述输入（初始状态） -->
    <div class="input-section" v-if="messages.length === 0">
      <h2>您好，请描述您遇到的问题</h2>
      <div class="input-box">
        <el-input
          v-model="userInput"
          type="textarea"
          :rows="3"
          placeholder="例如：我的电脑无法开机、打印机无法打印、邮箱无法登录..."
          @keyup.enter.ctrl="sendMessage"
        />
        <div class="input-actions">
          <el-upload
            :show-file-list="false"
            :before-upload="handleFileSelect"
            accept="image/*,.pdf,.doc,.docx,.xls,.xlsx,.txt,.zip,.rar"
          >
            <el-button :icon="Paperclip">附件</el-button>
          </el-upload>
          <el-button type="primary" size="large" @click="sendMessage" :loading="sending" :disabled="(!userInput.trim() && !pendingFile) || streaming">
            发送问题
          </el-button>
        </div>
        <!-- 待发送文件预览 -->
        <div v-if="pendingFile" class="pending-file">
          <el-tag closable @close="pendingFile = null">
            <el-icon><Document /></el-icon>
            {{ pendingFile.name }}
          </el-tag>
        </div>
      </div>
    </div>

    <!-- 快捷分类（初始状态） -->
    <div class="categories" v-if="messages.length === 0">
      <h3>常见问题分类</h3>
      <div class="category-grid">
        <div v-for="cat in categories" :key="cat.name" class="category-card" @click="quickAsk(cat.name)">
          <div class="cat-icon">{{ cat.icon }}</div>
          <div class="cat-name">{{ cat.name }}</div>
        </div>
      </div>
    </div>

    <!-- 对话区域（对话开始后显示） -->
    <div class="chat-section" v-if="messages.length > 0">
      <div class="chat-topbar">
        <span class="chat-topbar-title">💬 智能客服</span>
        <el-button text type="danger" @click="resetChat" size="small">
          <el-icon><Delete /></el-icon> 重置对话
        </el-button>
      </div>
      <div class="chat-window">
        <!-- 消息列表 - 可滚动区域 -->
        <div class="chat-messages" ref="chatRef">
          <div v-for="(msg, i) in messages" :key="i" :class="['msg', msg.role]">
            <div class="msg-avatar">{{ msg.role === 'user' ? '我' : '🤖' }}</div>
            <div class="msg-content">
              <!-- 图片消息 -->
              <div v-if="msg.type === 'image'" class="msg-image">
                <el-image :src="msg.url" :preview-src-list="[msg.url]" fit="contain" style="max-width: 300px; max-height: 300px; border-radius: 8px;" />
              </div>
              <!-- 文件消息 -->
              <div v-else-if="msg.type === 'file'" class="msg-file">
                <el-icon :size="24"><Document /></el-icon>
                <div class="file-info">
                  <span class="file-name">{{ msg.fileName }}</span>
                  <a :href="msg.url" target="_blank" download class="download-link">下载</a>
                </div>
              </div>
              <!-- 文本消息 -->
              <div v-else-if="msg.type === 'text'" class="msg-text" style="white-space: pre-wrap;">{{ msg.text }}<span v-if="msg.streaming" class="streaming-cursor">|</span></div>
              <!-- 思考过程 -->
              <div v-if="msg.thinking" class="thinking-section">
                <div class="thinking-header" @click="msg.thinkingExpanded = !msg.thinkingExpanded">
                  <span class="thinking-icon">🧠</span>
                  <span class="thinking-label">思考过程</span>
                  <span v-if="msg.thinkingActive" class="thinking-active">思考中...</span>
                  <span v-else class="thinking-toggle">{{ msg.thinkingExpanded ? '收起' : '展开' }}</span>
                </div>
                <div v-if="msg.thinkingExpanded || msg.thinkingActive" class="thinking-content">
                  {{ msg.thinking }}
                </div>
              </div>
              <!-- 来源引用卡片 -->
              <div v-if="msg.sources && msg.sources.length > 0" class="sources-card">
                <div class="sources-title">📎 参考来源</div>
                <div v-for="(src, si) in msg.sources" :key="si" class="source-item">
                  <el-tag size="small" :type="src.type === 'faq' ? 'success' : src.type === 'sop' ? 'warning' : 'info'" style="margin-right: 6px;">
                    {{ src.type === 'faq' ? 'FAQ' : src.type === 'sop' ? 'SOP' : '历史工单' }}
                  </el-tag>
                  <span class="source-title">{{ src.title || src.question || '未知来源' }}</span>
                </div>
              </div>
              <!-- 转人工按钮 -->
              <div v-if="msg.showTransfer" class="transfer-area">
                <el-button type="warning" size="small" @click="handleTransferToHuman(msg)">
                  🙋 转人工客服
                </el-button>
              </div>
              <!-- 工单链接 -->
              <div v-if="msg.ticketId" class="ticket-link-area">
                <el-button type="primary" @click="router.push('/chat-rooms')">
                  💬 进入聊天室
                </el-button>
                <el-button @click="router.push('/my-tickets')">
                  📋 查看我的工单
                </el-button>
              </div>
              <!-- 分类选择消息 -->
              <div v-if="msg.type === 'category-select'">
                <div class="msg-text" style="white-space: pre-wrap;">{{ msg.text }}</div>
                <div class="category-select-area">
                  <div v-for="cat in problemCategories" :key="cat.id" class="category-select-card" @click="handleCategorySelect(cat)">
                    <div class="cat-sel-icon">{{ cat.icon }}</div>
                    <div class="cat-sel-info">
                      <div class="cat-sel-name">{{ cat.name }}</div>
                      <div class="cat-sel-desc">{{ cat.desc }}</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
          <div v-if="waitingReply" class="msg bot">
            <div class="msg-avatar">🤖</div>
            <div class="msg-content"><div class="msg-text typing">正在思考...</div></div>
          </div>
        </div>

        <!-- 评价卡片 -->
        <div v-if="showRating" class="rating-card">
          <h3>请对本次服务进行评价</h3>
          <el-rate v-model="rating" size="large" />
          <el-input v-model="ratingComment" type="textarea" :rows="2" placeholder="请输入评价（可选）" style="margin: 12px 0" />
          <el-button type="primary" @click="submitRating">提交评价</el-button>
        </div>

        <!-- 输入框 - 固定在底部 -->
        <div class="chat-input-area">
          <div class="input-row">
            <el-upload
              :show-file-list="false"
              :before-upload="handleFileSelect"
              accept="image/*,.pdf,.doc,.docx,.xls,.xlsx,.txt,.zip,.rar"
            >
              <el-button :icon="Paperclip" circle />
            </el-upload>
            <el-input
              v-model="userInput"
              placeholder="输入消息... (Ctrl+Enter发送)"
              @keyup.enter.ctrl="sendMessage"
              :disabled="sending || streaming"
            />
            <el-button type="primary" @click="sendMessage" :loading="sending" :disabled="(!userInput.trim() && !pendingFile) || streaming">
              发送
            </el-button>
          </div>
          <!-- 待发送文件预览 -->
          <div v-if="pendingFile" class="pending-file-chat">
            <el-tag closable @close="pendingFile = null" size="small">
              <el-icon><Document /></el-icon>
              {{ pendingFile.name }}
            </el-tag>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/store/user'
import { ticketApi, chatApi, uploadApi, categoryApi, aiApi } from '@/api'
import { ElMessage } from 'element-plus'
import { Paperclip, Document, Delete } from '@element-plus/icons-vue'

const router = useRouter()
const store = useUserStore()
const userInput = ref('')
const sending = ref(false)
const waitingReply = ref(false)
const chatRef = ref(null)
const pendingFile = ref(null)
const streaming = ref(false)
let streamAbortController = null

// 消息持久化
const STORAGE_KEY = 'home_chat_messages'
const ROOM_KEY = 'home_chat_room_id'
const TICKET_KEY = 'home_chat_ticket_id'
const SESSION_ID_KEY = 'home_chat_session_id'

let parsedMessages = []
try { parsedMessages = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]') } catch (e) { parsedMessages = [] }
const messages = ref(parsedMessages)
const currentRoomId = ref(localStorage.getItem(ROOM_KEY) || null)
const currentTicketId = ref(localStorage.getItem(TICKET_KEY) || null)

// 会话 ID（服务端记忆系统使用）
function generateSessionId() {
  return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`
}
const sessionId = ref(localStorage.getItem(SESSION_ID_KEY) || generateSessionId())
localStorage.setItem(SESSION_ID_KEY, sessionId.value)

// 消息变化时自动保存
watch(messages, (val) => {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(val))
}, { deep: true })

watch(currentRoomId, (val) => {
  if (val) localStorage.setItem(ROOM_KEY, val)
  else localStorage.removeItem(ROOM_KEY)
})

watch(currentTicketId, (val) => {
  if (val) localStorage.setItem(TICKET_KEY, val)
  else localStorage.removeItem(TICKET_KEY)
})

// 重置对话
function resetChat() {
  messages.value = []
  currentRoomId.value = null
  currentTicketId.value = null
  localStorage.removeItem(STORAGE_KEY)
  localStorage.removeItem(ROOM_KEY)
  localStorage.removeItem(TICKET_KEY)
  // 生成新的会话 ID（服务端记忆重新开始）
  sessionId.value = generateSessionId()
  localStorage.setItem(SESSION_ID_KEY, sessionId.value)
}

// 评价
const showRating = ref(false)
const rating = ref(5)
const ratingComment = ref('')

const categories = [
  { name: '操作系统', icon: '💻' },
  { name: '邮件系统', icon: '📧' },
  { name: '网络问题', icon: '🌐' },
  { name: '硬件故障', icon: '🖨️' },
  { name: '账号权限', icon: '🔑' },
  { name: '软件安装', icon: '📦' },
]

// 问题分类选项（转人工时显示）
const defaultProblemCategories = [
  { id: 1, name: '桌面问题', icon: '🖥️', desc: '系统故障、蓝屏、无法开机等' },
  { id: 3, name: '网络问题', icon: '🌐', desc: '网络连接、WiFi、VPN等' },
  { id: 6, name: '软件问题', icon: '💻', desc: '软件安装、更新、邮箱等' },
  { id: 7, name: '其他问题', icon: '📋', desc: '账号权限、密码重置、咨询等' },
]
const problemCategories = ref(defaultProblemCategories)

const categoryIcons = { '桌面问题': '🖥️', '网络问题': '🌐', '软件问题': '💻', '其他问题': '📋', '硬件故障': '🖨️', '账号权限': '🔑', '邮箱问题': '📧', '操作系统': '💻' }
const categoryDescs = { '桌面问题': '系统故障、蓝屏、无法开机等', '网络问题': '网络连接、WiFi、VPN等', '软件问题': '软件安装、更新、邮箱等', '其他问题': '账号权限、密码重置、咨询等' }

async function loadCategories() {
  try {
    const cats = await categoryApi.getCategories()
    if (Array.isArray(cats) && cats.length > 0) {
      problemCategories.value = cats.map(c => ({
        id: c.id,
        name: c.name,
        icon: categoryIcons[c.name] || '📋',
        desc: categoryDescs[c.name] || c.description || c.name,
      }))
    }
  } catch (e) {
    // 普通用户无 admin 权限，使用默认列表
  }
}

onMounted(loadCategories)

// 机器人回复逻辑
const botReplies = {
  '蓝屏': '蓝屏问题通常是由于系统文件损坏或驱动冲突导致的。建议您：\n1. 尝试重启电脑\n2. 如果频繁蓝屏，记录错误代码\n3. 可以联系IT人员远程协助',
  '无法开机': '无法开机可能的原因：\n1. 检查电源线是否连接正常\n2. 尝试长按电源键10秒强制重启\n3. 如果有蜂鸣声，可能是硬件故障',
  '打印机': '打印机问题排查：\n1. 检查打印机是否开机并连接网络\n2. 确认打印机是否有纸张和墨盒\n3. 尝试重启打印机',
  '密码': '密码相关问题：\n1. 如果忘记密码，可以联系IT重置\n2. 如果密码过期，需要修改新密码\n3. 新密码需要包含大小写字母和数字',
  '邮件': '邮件问题排查：\n1. 检查网络连接是否正常\n2. 确认邮箱地址和密码是否正确\n3. 尝试重启邮件客户端',
  '网络': '网络问题排查：\n1. 检查WiFi是否已连接\n2. 尝试重启路由器\n3. 检查是否可以访问其他网站',
}

// 转人工关键词
const transferKeywords = ['转人工', '人工服务', '人工客服', '提交工单', '报障', '报修', '创建工单']

function getBotReply(text) {
  // 检测转人工关键词 → 返回分类选择标记
  for (const keyword of transferKeywords) {
    if (text.includes(keyword)) {
      return { type: 'category-select', text: '好的，我来帮您创建工单。请先选择问题类型：' }
    }
  }
  for (const [keyword, reply] of Object.entries(botReplies)) {
    if (text.includes(keyword)) return reply
  }
  return '感谢您的描述。为了更好地帮助您，请提供更多细节：\n1. 问题是什么时候开始的？\n2. 是否有错误提示信息？\n3. 是否可以截图？\n\n如果需要人工帮助，请输入"转人工"。'
}

// 处理分类选择 - 创建工单 + 聊天室
async function handleCategorySelect(category) {
  // 添加用户选择消息
  messages.value.push({ role: 'user', text: `${category.icon} ${category.name}`, type: 'text' })
  scrollToBottom(true)

  waitingReply.value = true
  try {
    // 1. 创建工单
    const ticket = await ticketApi.create({
      title: `${category.name} - ${messages.value.find(m => m.role === 'user')?.text?.substring(0, 30) || category.name}`,
      description: messages.value.map(m => {
        if (m.type === 'image') return '用户: [图片]'
        if (m.type === 'file') return `用户: [文件] ${m.fileName}`
        if (m.type === 'category-select') return ''
        return `${m.role === 'user' ? '用户' : '机器人'}: ${m.text}`
      }).filter(Boolean).join('\n'),
      category_id: category.id,
    })
    currentTicketId.value = ticket.id

    // 2. 立即创建聊天室
    try {
      const room = await chatApi.createRoom(ticket.id)
      currentRoomId.value = room.id
    } catch (e) {
      // 如果已存在，尝试获取
      try {
        const room = await chatApi.getRoom(ticket.id)
        currentRoomId.value = room.id
      } catch (e2) { /* 忽略 */ }
    }

    // 3. 显示工单信息和聊天室链接
    messages.value.push({
      role: 'bot',
      text: `✅ 工单创建成功！\n\n工单号：${ticket.ticket_no}\n类型：${category.name}\n状态：等待客服接单\n\n`,
      type: 'text',
      ticketId: ticket.id,
      ticketNo: ticket.ticket_no,
    })
  } catch (e) {
    messages.value.push({ role: 'bot', text: '创建工单失败，请稍后重试。', type: 'text' })
  }
  waitingReply.value = false
  scrollToBottom()
}

function isNearBottom() {
  if (!chatRef.value) return true
  const el = chatRef.value
  return el.scrollHeight - el.scrollTop - el.clientHeight < 100
}

function scrollToBottom(force = false) {
  nextTick(() => {
    if (!chatRef.value) return
    // 只在用户已经在底部时自动滚动，或强制滚动时
    if (force || isNearBottom()) {
      chatRef.value.scrollTop = chatRef.value.scrollHeight
    }
  })
}

function handleFileSelect(file) {
  // 检查文件大小 (10MB)
  if (file.size > 10 * 1024 * 1024) {
    ElMessage.warning('文件大小不能超过10MB')
    return false
  }
  pendingFile.value = file
  return false // 阻止自动上传
}

async function uploadFile(file) {
  try {
    const result = await uploadApi.upload(file)
    return result
  } catch (e) {
    ElMessage.error('文件上传失败')
    return null
  }
}

async function sendMessage() {
  const text = userInput.value.trim()
  const file = pendingFile.value

  // 如果有文件，先上传
  if (file) {
    sending.value = true
    const uploadResult = await uploadFile(file)
    sending.value = false

    if (uploadResult) {
      // 判断是图片还是文件
      const isImage = file.type.startsWith('image/')
      messages.value.push({
        role: 'user',
        type: isImage ? 'image' : 'file',
        url: uploadResult.url,
        fileName: file.name,
        text: isImage ? '' : file.name,
      })

      // 如果已创建工单聊天室，发送到聊天
      if (currentRoomId.value) {
        try {
          await chatApi.sendMessage(currentRoomId.value, {
            content: isImage ? `[图片] ${uploadResult.url}` : `[文件] ${file.name}\n${uploadResult.url}`,
            msg_type: isImage ? 'image' : 'text',
          })
        } catch (e) { ElMessage.error('发送失败：' + (e.response?.data?.detail || e.message)) }
      }
    }
    pendingFile.value = null
    scrollToBottom()
  }

  // 发送文本消息
  if (!text) return

  // 检测转人工关键词 → 走原有工单流程
  for (const keyword of transferKeywords) {
    if (text.includes(keyword)) {
      messages.value.push({ role: 'user', text, type: 'text' })
      userInput.value = ''
      scrollToBottom(true)
      messages.value.push({ role: 'bot', text: '好的，我来帮您创建工单。请先选择问题类型：', type: 'category-select' })
      scrollToBottom()
      return
    }
  }

  // 正常消息 → 调用 AI 智能客服
  messages.value.push({ role: 'user', text, type: 'text' })
  userInput.value = ''
  scrollToBottom(true)

  // 会话记忆由服务端管理，不再构建 history
  // 只保留兼容字段（session_id 优先）

  // 添加 AI 消息占位（流式追加）
  const aiMsgIndex = messages.value.length
  messages.value.push({
    role: 'bot',
    text: '',
    type: 'text',
    sources: null,
    showTransfer: false,
    streaming: true,
    thinking: null,
    thinkingActive: false,
    thinkingExpanded: false,
  })

  sending.value = true
  streaming.value = true
  streamAbortController = new AbortController()

  try {
    const token = localStorage.getItem('token')
    const response = await aiApi.chatStream({ question: text, session_id: sessionId.value }, token, streamAbortController.signal)

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          const payload = JSON.parse(line.slice(6))
          if (payload.type === 'sources') {
            messages.value[aiMsgIndex].sources = payload.sources || []
          } else if (payload.type === 'thinking') {
            if (!messages.value[aiMsgIndex].thinking) {
              messages.value[aiMsgIndex].thinking = ''
            }
            messages.value[aiMsgIndex].thinking += payload.content || ''
            messages.value[aiMsgIndex].thinkingActive = true
            messages.value[aiMsgIndex].thinkingExpanded = true
            scrollToBottom()
          } else if (payload.type === 'token') {
            messages.value[aiMsgIndex].thinkingActive = false
            messages.value[aiMsgIndex].text += payload.content || ''
            scrollToBottom()
          } else if (payload.type === 'done') {
            messages.value[aiMsgIndex].thinkingActive = false
            if (messages.value[aiMsgIndex].thinking) {
              messages.value[aiMsgIndex].thinkingExpanded = false
            }
            if (payload.has_relevant_docs === false) {
              messages.value[aiMsgIndex].showTransfer = true
            }
            if (payload.sources && !messages.value[aiMsgIndex].sources) {
              messages.value[aiMsgIndex].sources = payload.sources
            }
          } else if (payload.type === 'error') {
            messages.value[aiMsgIndex].text = payload.content || payload.message || 'AI 服务暂时不可用'
          }
        } catch (e) { /* ignore parse error */ }
      }
    }

    // 如果 AI 返回空内容，走 fallback
    if (!messages.value[aiMsgIndex].text) {
      messages.value[aiMsgIndex].text = getBotReply(text)
    }
  } catch (e) {
    if (e.name === 'AbortError') {
      messages.value[aiMsgIndex].text += '\n\n[已停止生成]'
    } else {
      // AI 接口不可用，fallback 到本地回复
      const reply = getBotReply(text)
      if (typeof reply === 'object' && reply.type === 'category-select') {
        messages.value[aiMsgIndex].text = reply.text
        messages.value[aiMsgIndex].type = 'category-select'
      } else {
        messages.value[aiMsgIndex].text = reply
      }
    }
  } finally {
    messages.value[aiMsgIndex].streaming = false
    sending.value = false
    streaming.value = false
    streamAbortController = null
    scrollToBottom()
  }
}

function quickAsk(catName) {
  userInput.value = `我的${catName}出了问题`
  sendMessage()
}

async function submitRating() {
  if (!currentTicketId.value) return
  try {
    await ticketApi.rate(currentTicketId.value, {
      rating: rating.value,
      rating_comment: ratingComment.value,
    })
    showRating.value = false
    messages.value.push({ role: 'bot', text: '感谢您的评价！如有其他问题，随时联系我们。', type: 'text' })
    ElMessage.success('评价成功')
  } catch (e) { ElMessage.error('评价失败') }
}

function handleTransferToHuman(msg) {
  msg.showTransfer = false
  messages.value.push({ role: 'bot', text: '好的，我来帮您创建工单。请先选择问题类型：', type: 'category-select' })
  scrollToBottom()
}
</script>

<style scoped>
.home { padding: 24px 0; }

/* 初始输入区域 */
.input-section { text-align: center; margin-bottom: 32px; }
.input-section h2 { color: #1a365d; margin-bottom: 24px; font-size: 24px; }
.input-box { max-width: 600px; margin: 0 auto; }
.input-actions { display: flex; gap: 12px; margin-top: 12px; justify-content: center; }
.input-actions .el-button { min-width: 100px; }

/* 聊天区域 */
.chat-section {
  max-width: 700px;
  margin: 0 auto;
  height: calc(100vh - 160px);
  display: flex;
  flex-direction: column;
}

/* 顶部操作栏 */
.chat-topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 16px;
  background: white;
  border-radius: 12px 12px 0 0;
  border-bottom: 1px solid #eee;
}
.chat-topbar-title { font-size: 14px; font-weight: 600; color: #333; }

.chat-window {
  flex: 1;
  background: white;
  border-radius: 12px;
  box-shadow: 0 2px 12px rgba(0,0,0,0.08);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

/* 输入框区域 */
.chat-input-area {
  padding: 12px 16px;
  border-top: 1px solid #eee;
  background: #fafafa;
  flex-shrink: 0;
}

.input-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.input-row .el-input {
  flex: 1;
}

/* 待发送文件 */
.pending-file { margin-top: 8px; }
.pending-file-chat { margin-top: 8px; }

/* 消息样式 */
.msg { display: flex; gap: 12px; margin-bottom: 16px; }
.msg.user { flex-direction: row-reverse; }
.msg-avatar { width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 14px; flex-shrink: 0; }
.msg.user .msg-avatar { background: #2563eb; color: white; }
.msg.bot .msg-avatar { background: #f0f0f0; }
.msg-content { max-width: 70%; }

/* 文本消息 */
.msg-text { padding: 12px 16px; border-radius: 12px; font-size: 14px; line-height: 1.6; white-space: pre-wrap; }
.msg.user .msg-text { background: #2563eb; color: white; border-bottom-right-radius: 4px; }
.msg.bot .msg-text { background: #f5f5f5; color: #333; border-bottom-left-radius: 4px; }
.typing { color: #999; }

/* 图片消息 */
.msg-image { max-width: 300px; }
.msg.user .msg-image { margin-left: auto; }

/* 文件消息 */
.msg-file { display: flex; align-items: center; gap: 12px; padding: 12px 16px; background: #f5f5f5; border-radius: 12px; }
.msg.user .msg-file { background: #2563eb; color: white; }
.file-info { display: flex; flex-direction: column; gap: 4px; }
.file-name { font-size: 14px; font-weight: 500; }
.download-link { font-size: 12px; color: #2563eb; text-decoration: none; }
.msg.user .download-link { color: white; }

/* 评价卡片 */
.rating-card { padding: 24px; border-top: 1px solid #eee; text-align: center; }
.rating-card h3 { margin-bottom: 16px; color: #333; }

/* 分类 */
.categories { max-width: 600px; margin: 0 auto; }
.categories h3 { color: #333; margin-bottom: 16px; }
.category-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.category-card { background: white; border-radius: 12px; padding: 20px; text-align: center; cursor: pointer; transition: all 0.2s; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
.category-card:hover { transform: translateY(-2px); box-shadow: 0 4px 16px rgba(0,0,0,0.12); }
.cat-icon { font-size: 32px; margin-bottom: 8px; }
.cat-name { font-size: 14px; color: #333; }

/* 分类选择卡片（转人工时弹出） */
.category-select-area { display: flex; flex-direction: column; gap: 8px; margin-top: 12px; }
.category-select-card { display: flex; align-items: center; gap: 12px; padding: 12px 16px; background: #f0f7ff; border: 1px solid #d6e8fa; border-radius: 10px; cursor: pointer; transition: all 0.2s; }
.category-select-card:hover { background: #d6e8fa; border-color: #409eff; transform: translateX(4px); }
.cat-sel-icon { font-size: 28px; }
.cat-sel-info { flex: 1; }
.cat-sel-name { font-size: 15px; font-weight: 600; color: #333; }
.cat-sel-desc { font-size: 12px; color: #888; margin-top: 2px; }

/* 工单链接区域 */
.ticket-link-area { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }

/* 流式光标 */
.streaming-cursor {
  animation: blink 0.8s step-end infinite;
  color: #2563eb;
  font-weight: bold;
}
@keyframes blink {
  50% { opacity: 0; }
}

/* 来源引用卡片 */
.sources-card {
  margin-top: 8px;
  padding: 10px 14px;
  background: #f0f7ff;
  border: 1px solid #d6e8fa;
  border-radius: 10px;
  font-size: 13px;
}
.sources-title {
  font-weight: 600;
  color: #333;
  margin-bottom: 6px;
  font-size: 13px;
}
.source-item {
  display: flex;
  align-items: center;
  padding: 3px 0;
  color: #555;
}
.source-title {
  font-size: 13px;
  color: #555;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 转人工按钮区域 */
.transfer-area {
  margin-top: 10px;
}

/* 思考过程 */
.thinking-section {
  margin-top: 8px;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  overflow: hidden;
}
.thinking-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  background: #f8f9fa;
  cursor: pointer;
  font-size: 13px;
  color: #666;
  user-select: none;
}
.thinking-header:hover {
  background: #eef0f2;
}
.thinking-icon {
  font-size: 14px;
}
.thinking-label {
  font-weight: 500;
}
.thinking-active {
  color: #409eff;
  font-size: 12px;
  animation: thinking-pulse 1.5s ease-in-out infinite;
}
@keyframes thinking-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
.thinking-toggle {
  margin-left: auto;
  font-size: 12px;
  color: #999;
}
.thinking-content {
  padding: 10px 12px;
  font-size: 12px;
  color: #666;
  line-height: 1.5;
  white-space: pre-wrap;
  background: #fafbfc;
  border-top: 1px solid #e0e0e0;
  line-height: 1.6;
  color: #888;
  background: #fafafa;
  border-top: 1px solid #e0e0e0;
  white-space: pre-wrap;
  max-height: 300px;
  overflow-y: auto;
}
</style>
