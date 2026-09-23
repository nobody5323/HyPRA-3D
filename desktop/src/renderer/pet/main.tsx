import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./pet.css";

const container = document.getElementById("pet-root");

if (!container) {
  throw new Error("桌宠页面缺少 #pet-root 容器");
}

createRoot(container).render(<App />);
