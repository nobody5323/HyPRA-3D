/** @type {import('next').NextConfig} */
const nextConfig = {
  // standalone：供 Docker 多阶段构建只拷贝裁剪后的产物（约 200MB，而非全量 node_modules）
  // 不影响 npm run dev / next start 的本地行为
  output: "standalone",
  // 后端 API 地址（浏览器发起请求，需填**宿主机可达**地址，如 http://localhost:8000）
  env: {
    NEXT_PUBLIC_API_BASE: process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000",
  },
  eslint: {
    // 参赛演示优先保证构建可复现；lint 单独跑
    ignoreDuringBuilds: true,
  },
};

export default nextConfig;
