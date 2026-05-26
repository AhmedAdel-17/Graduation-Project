import { useState } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";
import { Trash2, Eye, Search } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

import { api } from "@/lib/api";
import { DECISION_COLORS } from "@/lib/constants";
import { formatRelative } from "@/lib/formatters";
import { cn } from "@/lib/utils";

export function HistoryPage() {
  const [tickerQ, setTickerQ] = useState("");
  const [decision, setDecision] = useState("all");
  const [type, setType] = useState("all");
  const [sort, setSort] = useState<"newest" | "oldest" | "confidence">("newest");

  const qc = useQueryClient();
  const navigate = useNavigate();
  const { data, isLoading } = useQuery({
    queryKey: ["runs", { tickerQ, decision, type, sort }],
    queryFn: () => api.runs.list({ ticker: tickerQ, decision, type, sort, page: 1, page_size: 50 }),
  });

  const del = useMutation({
    mutationFn: (id: string) => api.runs.delete(id),
    onSuccess: () => {
      toast.success("Run deleted");
      qc.invalidateQueries({ queryKey: ["runs"] });
    },
  });

  const items = data?.items ?? [];

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">History</h1>
        <p className="text-sm text-muted-foreground">All your past predictions and backtests.</p>
      </div>

      <Card>
        <CardContent className="flex flex-wrap items-center gap-3 p-4">
          <div className="relative min-w-[200px] flex-1">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search ticker…"
              value={tickerQ}
              onChange={(e) => setTickerQ(e.target.value)}
              className="pl-8"
            />
          </div>
          <Select value={decision} onValueChange={setDecision}>
            <SelectTrigger className="w-[140px]"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All decisions</SelectItem>
              <SelectItem value="BUY">BUY</SelectItem>
              <SelectItem value="SELL">SELL</SelectItem>
              <SelectItem value="HOLD">HOLD</SelectItem>
            </SelectContent>
          </Select>
          <Select value={type} onValueChange={setType}>
            <SelectTrigger className="w-[140px]"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All types</SelectItem>
              <SelectItem value="prediction">Prediction</SelectItem>
            </SelectContent>
          </Select>
          <Select value={sort} onValueChange={(v: any) => setSort(v)}>
            <SelectTrigger className="w-[160px]"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="newest">Newest</SelectItem>
              <SelectItem value="oldest">Oldest</SelectItem>
              <SelectItem value="confidence">Highest confidence</SelectItem>
            </SelectContent>
          </Select>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-10" />)}
            </div>
          ) : items.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2 p-12 text-center">
              <div className="text-sm font-medium">No runs yet</div>
              <div className="text-xs text-muted-foreground">Try running your first analysis.</div>
              <Link to="/run"><Button size="sm" className="mt-2">Run analysis</Button></Link>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>When</TableHead>
                  <TableHead>Ticker</TableHead>
                  <TableHead>Decision</TableHead>
                  <TableHead>Confidence</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((r) => {
                  const c = r.decision ? DECISION_COLORS[r.decision] : null;
                  return (
                    <TableRow
                      key={r.id}
                      onClick={() => navigate({ to: "/history/$id", params: { id: r.id } })}
                      className="cursor-pointer hover:bg-secondary/50"
                    >
                      <TableCell className="text-muted-foreground">{formatRelative(r.created_at)}</TableCell>
                      <TableCell className="font-mono text-sm">{r.ticker}</TableCell>
                      <TableCell>
                        {r.decision && c ? (
                          <Badge className={cn(c.bg, c.text, "font-mono text-[10px]")}>{r.decision}</Badge>
                        ) : (
                          <span className="text-xs text-muted-foreground">{r.status}</span>
                        )}
                      </TableCell>
                      <TableCell className="font-mono tabular text-sm">
                        {r.confidence ? `${(r.confidence * 100).toFixed(0)}%` : "—"}
                      </TableCell>
                      <TableCell className="text-right">
                        <div
                          className="flex justify-end gap-1"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <Button
                            variant="ghost"
                            size="sm"
                            title="View run"
                            onClick={() => navigate({ to: "/history/$id", params: { id: r.id } })}
                          >
                            <Eye className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            title="Delete run"
                            onClick={() => del.mutate(r.id)}
                          >
                            <Trash2 className="h-4 w-4 text-destructive" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
