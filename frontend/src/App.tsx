import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { Toaster } from "sonner";
import { Navigate, Route, Routes, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import UploadPage from "./pages/Upload";
import GrantsPage from "./pages/Grants";
import ChatPage from "./pages/Chat";
import LoginPage from "./pages/Login";
import { getMe, logout, Me } from "./api/auth";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

// 只允许站内相对路径，防止 redirect 参数被用于跳转到外部站点
function safeRedirect(raw: string | null): string {
  return raw && raw.startsWith("/") && !raw.startsWith("//") && !raw.startsWith("/login") ? raw : "/chat";
}

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const [me, setMe] = useState<Me | null>(null);
  const [checking, setChecking] = useState(true);

  const refreshMe = useCallback(async () => {
    try {
      setMe(await getMe());
    } catch {
      setMe(null);
    }
  }, []);

  useEffect(() => {
    refreshMe().finally(() => setChecking(false));
  }, [refreshMe]);

  const onLogout = async () => {
    try {
      await logout();
    } finally {
      setMe(null);
      navigate("/login", { replace: true });
    }
  };

  if (checking) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (location.pathname.startsWith("/login")) {
    return me ? (
      <Navigate to={safeRedirect(params.get("redirect"))} replace />
    ) : (
      <>
        <LoginPage onSuccess={refreshMe} />
        <Toaster position="top-center" richColors />
      </>
    );
  }

  if (!me) {
    const redirect = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?redirect=${redirect}`} replace />;
  }

  const selected = location.pathname.startsWith("/upload")
    ? "/upload"
    : location.pathname.startsWith("/grants")
      ? "/grants"
      : "/chat";

  return (
    <div className="flex h-screen flex-col">
      <header className="app-header">
        <div className="brand">
          <span className="brand-mark">账</span>
          <span className="brand-name">
            机票经营<em>台账</em>
          </span>
          <span className="brand-tag">Demo</span>
        </div>
        <Tabs value={selected} onValueChange={(v) => navigate(v)}>
          <TabsList>
            <TabsTrigger value="/chat">问答</TabsTrigger>
            <TabsTrigger value="/upload">导入记录</TabsTrigger>
            <TabsTrigger value="/grants">数据授权</TabsTrigger>
          </TabsList>
        </Tabs>
        <div className="flex items-center gap-2">
          <span className="text-sm">{me.display_name}</span>
          <Button variant="outline" size="sm" onClick={onLogout}>
            退出
          </Button>
        </div>
      </header>
      <main className="relative z-[1] min-h-0 flex-1 overflow-hidden">
        <Routes>
          <Route path="/" element={<Navigate to="/chat" replace />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/chat/:id" element={<ChatPage />} />
          <Route path="/upload" element={<div className="h-full overflow-auto p-8"><UploadPage /></div>} />
          <Route path="/grants" element={<div className="h-full overflow-auto p-8"><GrantsPage /></div>} />
          {/* 未知路径（如旧的 /history 书签）统一回到问答页 */}
          <Route path="*" element={<Navigate to="/chat" replace />} />
        </Routes>
      </main>
      <Toaster position="top-center" richColors />
    </div>
  );
}
