/**
 * Parameterised Axios client factory shared across all frontends.
 *
 * Usage:
 *   import { createApiClient } from '@shared/api/request'
 *   const api = createApiClient()            // defaults: baseURL='/api', loginPath='/login'
 *   const api = createApiClient({ loginPath: '/auth/login' })
 */
import axios from 'axios'
import { ElMessage } from 'element-plus'

// 429 错误去重：避免批量请求时弹出大量"请求过于频繁"提示
let _rateLimitMsgShown = 0
function showRateLimitOnce(msg) {
  const now = Date.now()
  if (now - _rateLimitMsgShown > 3000) { // 3秒内只显示一次
    _rateLimitMsgShown = now
    ElMessage.warning(msg || '请求过于频繁，请稍后再试')
  }
}

// 429 自动重试配置
const MAX_RETRIES = 3
const RETRY_BASE_DELAY = 1000 // 1秒基础延迟

async function retryRequest(error) {
  const config = error.config
  if (!config || error.response?.status !== 429) {
    return Promise.reject(error)
  }

  // 初始化重试计数
  if (!config.__retryCount) {
    config.__retryCount = 0
  }

  if (config.__retryCount >= MAX_RETRIES) {
    return Promise.reject(error)
  }

  config.__retryCount += 1

  // 指数退避：1s, 2s, 4s
  const delay = RETRY_BASE_DELAY * Math.pow(2, config.__retryCount - 1)
  await new Promise(resolve => setTimeout(resolve, delay))

  // 重试请求
  return axios(config)
}

export function createApiClient({ baseURL = '/api', loginPath = '/login', timeout = 30000 } = {}) {
  const api = axios.create({ baseURL, timeout })

  api.interceptors.request.use(
    (config) => {
      const token = localStorage.getItem('token')
      if (token) config.headers.Authorization = `Bearer ${token}`
      return config
    },
    (error) => Promise.reject(error)
  )

  api.interceptors.response.use(
    (response) => response.data,
    async (error) => {
      const status = error.response?.status
      const msg = error.response?.data?.detail || '请求失败'
      const isLoginRequest = error.config?.url?.includes('/auth/login')
      const requireCaptcha = error.response?.headers?.['x-require-captcha'] === 'true'

      // 429 限流：自动重试（指数退避）
      if (status === 429 && !isLoginRequest) {
        try {
          const response = await retryRequest(error)
          return response.data
        } catch (retryError) {
          // 重试也失败了，显示提示
          showRateLimitOnce(msg)
          return Promise.reject(retryError)
        }
      }

      if (status === 401 && !isLoginRequest) {
        // 非登录接口的 401：token 过期，清除并跳转登录页
        localStorage.removeItem('token')
        localStorage.removeItem('user')
        localStorage.removeItem('permissions')
        window.location.href = loginPath
      } else if (isLoginRequest && requireCaptcha) {
        // 登录接口需要验证码：不显示错误，由调用方弹出验证码对话框
      } else if (status === 401 && isLoginRequest) {
        // 登录接口的 401：账号或密码错误，不跳转，只返回错误
        // 不显示 ElMessage，由调用方处理
      } else if (status === 403) {
        ElMessage.warning(msg)
      } else if (status !== 429) {
        // 非429错误正常显示（429已在上面处理）
        ElMessage.error(msg)
      }
      return Promise.reject(error)
    }
  )

  return api
}
