/**
 * PostCSS 配置：只为控制台窗服务。
 *
 * 桌宠窗不用 Tailwind（它是手写 `pet.css`，样式要精确控制到像素），
 * 控制台则**复用 Web 端的组件**，而那些组件的类名（`bg-surface-raised` 等）
 * 全靠 Tailwind + 自定义色板才能成立。
 *
 * @type {import('postcss-load-config').Config}
 */
const config = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};

export default config;
