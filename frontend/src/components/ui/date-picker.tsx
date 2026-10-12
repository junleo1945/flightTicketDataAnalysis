import { useState } from "react";
import { format, isValid, parse } from "date-fns";
import { CalendarDays } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

const DATE_FORMAT = "yyyy-MM-dd";

interface Props {
  // YYYY-MM-DD，空字符串表示未选择
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
}

// 弹出式日期选择：内部使用 Calendar，对外仍是 YYYY-MM-DD 字符串
export function DatePicker({ value, onChange, placeholder = "选择日期", className }: Props) {
  const [open, setOpen] = useState(false);
  const parsed = value ? parse(value, DATE_FORMAT, new Date()) : undefined;
  const selected = parsed && isValid(parsed) ? parsed : undefined;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" className={cn("w-44 justify-start font-normal", !selected && "text-muted-foreground", className)}>
          <CalendarDays />
          {selected ? format(selected, DATE_FORMAT) : placeholder}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-auto p-0">
        <Calendar
          mode="single"
          selected={selected}
          defaultMonth={selected}
          onSelect={(day) => {
            if (!day) return;
            onChange(format(day, DATE_FORMAT));
            setOpen(false);
          }}
        />
      </PopoverContent>
    </Popover>
  );
}
