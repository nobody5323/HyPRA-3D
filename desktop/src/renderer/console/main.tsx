import { createRoot } from "react-dom/client";

/*
 * 复用 Web 端的全局样式。
 *
 * 顺序要紧：先 globals.css（它带 `@tailwind base/components/utilities`，
 * 且定义了页面底色与焦点环），再是本窗的少量补充。
 */
import "@/app/globals.css";
import "./console.css";

import { ConsoleApp } from "./ConsoleApp";

const container = document.getElementById("console-root");

if (!container) {
  throw new Error("控制台页面缺少 #console-root 容器");
}

createRoot(container).render(<ConsoleApp />);
