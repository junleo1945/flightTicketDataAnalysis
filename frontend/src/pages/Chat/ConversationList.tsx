import { useState } from "react";
import { MoreHorizontal, Pencil, Plus, Star, Trash2 } from "lucide-react";
import type { ConversationSummary } from "../../api/conversations";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";

interface Props {
  items: ConversationSummary[] | null;
  activeId: number | null;
  disabled: boolean;
  onNew: () => void;
  onSelect: (id: number) => void;
  onRename: (id: number, title: string) => Promise<void>;
  onTogglePin: (id: number, pinned: boolean) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
}

export default function ConversationList(p: Props) {
  const [renaming, setRenaming] = useState<{ id: number; title: string } | null>(null);
  const [deleting, setDeleting] = useState<ConversationSummary | null>(null);

  const confirmRename = async () => {
    if (!renaming || !renaming.title.trim()) return;
    await p.onRename(renaming.id, renaming.title.trim());
    setRenaming(null);
  };

  const confirmDelete = async () => {
    if (!deleting) return;
    await p.onDelete(deleting.id);
    setDeleting(null);
  };

  const renderItem = (c: ConversationSummary) => {
    const active = c.id === p.activeId;
    return (
      <div
        key={c.id}
        className={cn(
          "group flex cursor-pointer items-center gap-1.5 rounded-lg py-1.5 pl-2.5 pr-1 text-sm text-muted-foreground hover:bg-muted",
          active && "bg-muted font-semibold text-foreground",
        )}
        onClick={() => !p.disabled && p.onSelect(c.id)}
      >
        <span className="min-w-0 flex-1 truncate" title={c.title}>
          {c.title}
        </span>
        {/* 菜单的点击会经由 React 事件冒泡到这里，必须阻止，否则会同时触发选中会话 */}
        <div onClick={(e) => e.stopPropagation()}>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                aria-label="更多操作"
                className={cn(
                  "rounded-md p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-border group-hover:opacity-100 data-[state=open]:opacity-100",
                  active && "opacity-100",
                )}
              >
                <MoreHorizontal className="size-4" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => p.onTogglePin(c.id, !c.pinned)}>
                <Star className={cn("size-4", c.pinned && "fill-current")} />
                {c.pinned ? "取消收藏" : "收藏"}
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => setRenaming({ id: c.id, title: c.title })}>
                <Pencil className="size-4" />
                重命名
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem destructive onSelect={() => setDeleting(c)}>
                <Trash2 className="size-4" />
                删除
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    );
  };

  const pinned = (p.items ?? []).filter((c) => c.pinned);
  const recent = (p.items ?? []).filter((c) => !c.pinned);

  return (
    <aside className="flex min-h-0 w-64 shrink-0 flex-col border-r border-border bg-card/40">
      <div className="p-3 pb-2.5">
        <Button variant="outline" className="w-full" onClick={p.onNew} disabled={p.disabled}>
          <Plus />
          新对话
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto px-2 pb-3">
        {p.items !== null && p.items.length === 0 && (
          <p className="px-3 py-8 text-center text-sm text-muted-foreground">还没有会话</p>
        )}
        {pinned.length > 0 && (
          <>
            <div className="px-2.5 pb-1 pt-3 text-xs text-muted-foreground">收藏</div>
            {pinned.map(renderItem)}
          </>
        )}
        {recent.length > 0 && (
          <>
            <div className="px-2.5 pb-1 pt-3 text-xs text-muted-foreground">最近</div>
            {recent.map(renderItem)}
          </>
        )}
      </div>

      <Dialog open={renaming !== null} onOpenChange={(open) => !open && setRenaming(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>重命名会话</DialogTitle>
          </DialogHeader>
          <Input
            autoFocus
            maxLength={200}
            value={renaming?.title ?? ""}
            onChange={(e) => setRenaming((r) => (r ? { ...r, title: e.target.value } : r))}
            onKeyDown={(e) => e.key === "Enter" && confirmRename()}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setRenaming(null)}>
              取消
            </Button>
            <Button onClick={confirmRename} disabled={!renaming?.title.trim()}>
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除这个会话？</DialogTitle>
            <DialogDescription>会话中的全部问答都会被删除，无法恢复。</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleting(null)}>
              取消
            </Button>
            <Button variant="destructive" onClick={confirmDelete}>
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </aside>
  );
}
