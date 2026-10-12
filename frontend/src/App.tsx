import { Layout, Segmented } from "antd";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import UploadPage from "./pages/Upload";
import ChatPage from "./pages/Chat";

const { Header, Content } = Layout;

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const selected = location.pathname.startsWith("/upload") ? "/upload" : "/chat";

  return (
    <Layout style={{ height: "100vh", background: "transparent" }}>
      <Header className="app-header">
        <div className="brand">
          <span className="brand-mark">账</span>
          <span className="brand-name">
            机票经营<em>台账</em>
          </span>
          <span className="brand-tag">Demo</span>
        </div>
        <Segmented
          value={selected}
          onChange={(v) => navigate(String(v))}
          options={[
            { label: "问答", value: "/chat" },
            { label: "导入记录", value: "/upload" },
          ]}
        />
      </Header>
      <Content style={{ flex: 1, minHeight: 0, overflow: "hidden", background: "transparent", position: "relative", zIndex: 1 }}>
        <Routes>
          <Route path="/" element={<Navigate to="/chat" replace />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/upload" element={<div style={{ height: "100%", overflow: "auto", padding: 32 }}><UploadPage /></div>} />
        </Routes>
      </Content>
    </Layout>
  );
}
