import { useEffect, useRef } from "react";
import { ArrowUp } from "lucide-react";
import { Button } from "@/components/ui/button";

interface Props {
  busy: boolean;
  text: string;
  onText: (t: string) => void;
  onSend: () => void;
}

const MAX_HEIGHT = 144; // 约 6 行

export default function Composer(p: Props) {
  const canSend = !p.busy && p.text.trim().length > 0;
  const ref = useRef<HTMLTextAreaElement>(null);

  // 输入框高度随内容增长，最多 6 行
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`;
  }, [p.text]);

  return (
    <div className="composer-wrap">
      <div className="composer">
        <div className="composer-box">
          <textarea
            ref={ref}
            rows={1}
            value={p.text}
            placeholder="问点什么，比如：2026-10-11 的订单量相比前 7 天怎么样？"
            onChange={(e) => p.onText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                if (canSend) p.onSend();
              }
            }}
            className="block w-full resize-none bg-transparent text-sm leading-6 outline-none placeholder:text-muted-foreground"
          />
          <div className="composer-bar">
            <div className="composer-tools">
              <span className="kbd-hint">Enter 发送 · Shift+Enter 换行</span>
            </div>
            <Button size="icon" className="rounded-full" onClick={p.onSend} disabled={!canSend} aria-label="发送">
              <ArrowUp />
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
