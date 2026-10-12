import { useState, type FormEvent } from "react";
import { Loader2, Lock, User } from "lucide-react";
import { login, loginError } from "../../api/auth";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

interface Props {
  // 登录成功后由 App 重新拉取当前用户
  onSuccess: () => Promise<void>;
}

export default function LoginPage({ onSuccess }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) {
      setError("请输入 ERP 账号和密码");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      await login(username.trim(), password);
      await onSuccess();
    } catch (err) {
      setError(loginError(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-full items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-xl">
            机票经营<em className="font-normal not-italic text-primary">台账</em>
          </CardTitle>
          <CardDescription>使用 ERP 账号登录</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="flex flex-col gap-4" autoComplete="on">
            {error && <Alert variant="destructive">{error}</Alert>}
            <label className="flex flex-col gap-1.5 text-sm font-medium">
              ERP 账号
              <div className="relative">
                <User className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  className="pl-9"
                  autoComplete="username"
                  autoFocus
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </div>
            </label>
            <label className="flex flex-col gap-1.5 text-sm font-medium">
              密码
              <div className="relative">
                <Lock className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  className="pl-9"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
            </label>
            <Button type="submit" disabled={submitting} className="w-full">
              {submitting && <Loader2 className="animate-spin" />}
              登录
            </Button>
            <p className="text-xs text-muted-foreground">
              上传的数据全员共享；问答记录仅本人可见。请勿在问答中输入密码等敏感信息。
            </p>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
