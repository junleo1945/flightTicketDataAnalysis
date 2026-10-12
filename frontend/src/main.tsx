import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { ConfigProvider } from "antd";
import App from "./App";
import "./theme.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider
      theme={{
        token: {
          colorPrimary: "#1f4d3a",
          colorBgLayout: "#f3eee4",
          colorBorder: "#d9d0bf",
          borderRadius: 10,
          fontFamily: "'Noto Sans SC', system-ui, sans-serif",
        },
        components: {
          Segmented: { itemSelectedBg: "#1c1a17", itemSelectedColor: "#f3eee4", trackBg: "#e8e0cf" },
        },
      }}
    >
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ConfigProvider>
  </React.StrictMode>,
);
