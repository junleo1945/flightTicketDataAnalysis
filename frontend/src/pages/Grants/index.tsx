import { useEffect, useState, type FormEvent } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { GrantList, GrantUser, addGrant, grantErrorMessage, listGrants, revokeGrant } from "../../api/grants";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

const EMPTY: GrantList = { granted_to_me: [], granted_by_me: [] };

function UserTable({ rows, action }: { rows: GrantUser[]; action?: (u: GrantUser) => React.ReactNode }) {
  return (
    <div className="overflow-hidden rounded-md border border-border">
      <table className="w-full text-sm">
        <thead className="bg-muted/60 text-left text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">显示名</th>
            <th className="px-3 py-2 font-medium">ERP 用户名</th>
            {action && <th className="px-3 py-2 text-right font-medium">操作</th>}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={action ? 3 : 2} className="px-3 py-6 text-center text-muted-foreground">
                暂无
              </td>
            </tr>
          )}
          {rows.map((u) => (
            <tr key={u.user_id} className="border-t border-border">
              <td className="px-3 py-2">{u.display_name}</td>
              <td className="px-3 py-2">{u.username}</td>
              {action && <td className="px-3 py-2 text-right">{action(u)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function GrantsPage() {
  const [data, setData] = useState<GrantList>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [username, setUsername] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const reload = () =>
    listGrants()
      .then(setData)
      .catch((err) => toast.error(grantErrorMessage(err)))
      .finally(() => setLoading(false));

  useEffect(() => {
    reload();
  }, []);

  const onAdd = async (e: FormEvent) => {
    e.preventDefault();
    const name = username.trim();
    if (!name) return;
    setSubmitting(true);
    try {
      const u = await addGrant(name);
      toast.success(`已授权给 ${u.display_name}（${u.username}）`);
      setUsername("");
      reload();
    } catch (err) {
      toast.error(grantErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  const onRevoke = async (u: GrantUser) => {
    if (!window.confirm(`确定撤销对 ${u.display_name}（${u.username}）的数据授权？撤销后对方立即看不到你的数据。`)) return;
    try {
      await revokeGrant(u.user_id);
      toast.success("已撤销授权");
      reload();
    } catch (err) {
      toast.error(grantErrorMessage(err));
    }
  };

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>数据授权</CardTitle>
          <CardDescription>
            授权后，对方可以只读查看你的订单、查询量、数据质量数据（利润、订单量、查询比分析及问答）。数据不会被复制，撤销即时生效。
            对方需要先登录过本系统，才能按用户名找到。
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <form onSubmit={onAdd} className="flex items-center gap-2">
            <Input
              placeholder="输入对方的 ERP 用户名"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              maxLength={128}
              className="max-w-xs"
            />
            <Button type="submit" disabled={submitting || !username.trim()}>
              {submitting && <Loader2 className="size-4 animate-spin" />}
              授权
            </Button>
          </form>

          {loading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="size-5 animate-spin text-muted-foreground" />
            </div>
          ) : (
            <>
              <section className="flex flex-col gap-2">
                <h3 className="text-sm font-medium">我授权出去的（{data.granted_by_me.length}）</h3>
                <UserTable
                  rows={data.granted_by_me}
                  action={(u) => (
                    <Button variant="outline" size="sm" onClick={() => onRevoke(u)}>
                      撤销
                    </Button>
                  )}
                />
              </section>
              <section className="flex flex-col gap-2">
                <h3 className="text-sm font-medium">别人授权给我的（{data.granted_to_me.length}）</h3>
                <UserTable rows={data.granted_to_me} />
              </section>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
