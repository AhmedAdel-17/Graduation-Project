import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { api } from "../../services/api/client";
import { cn, formatPercent } from "../../lib/utils";

interface SectorData {
  sector: string;
  change: number;
}

export function SectorHeatmap() {
  const [sectors, setSectors] = useState<SectorData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function fetchSectors() {
      try {
        setLoading(true);
        const res = await api.get<{ status: string; data: SectorData[] }>("/market/sectors");
        if (res.status === "ok") {
          // Filter out sectors with 0 or very small changes if they are 'Other' to keep it clean
          setSectors(res.data.filter(s => s.sector !== "Other" || Math.abs(s.change) > 0.01));
        } else {
          setError("Failed to load sector data");
        }
      } catch (err) {
        setError("Error fetching sectors");
        console.error(err);
      } finally {
        setLoading(false);
      }
    }
    fetchSectors();
  }, []);

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center p-10 card grain">
        <Loader2 className="h-6 w-6 animate-spin text-stone-400 mb-3" />
        <span className="text-stone-500 text-[13px]">Scanning sectors...</span>
      </div>
    );
  }

  if (error || sectors.length === 0) {
    return null;
  }

  // Calculate max absolute change for color intensity scaling
  const maxAbsChange = Math.max(...sectors.map(s => Math.abs(s.change)), 1);

  return (
    <div className="card overflow-hidden grain anim-fade-up" style={{ animationDelay: "100ms" }}>
      <div className="border-b border-stone-200 p-4 dark:border-[var(--hairline)]">
        <h3 className="text-[15px] font-semibold text-ink flex items-center gap-2">
          Sector Performance (1D)
        </h3>
      </div>
      
      <div className="p-4 grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
        {sectors.map((sector) => {
          const isPositive = sector.change > 0;
          const isNegative = sector.change < 0;
          
          // Calculate intensity 0-100%
          const intensity = Math.min((Math.abs(sector.change) / maxAbsChange) * 100, 100);
          
          // Generate a background color based on intensity
          // For positive: Emerald. For negative: Rose. For zero: Stone.
          let bgClass = "bg-stone-100 text-stone-700 border-stone-200";
          
          if (isPositive) {
            if (intensity > 75) bgClass = "bg-emerald-500 text-white border-emerald-600";
            else if (intensity > 40) bgClass = "bg-emerald-400 text-white border-emerald-500";
            else if (intensity > 15) bgClass = "bg-emerald-200 text-emerald-900 border-emerald-300";
            else bgClass = "bg-emerald-50 text-emerald-700 border-emerald-100";
          } else if (isNegative) {
            if (intensity > 75) bgClass = "bg-rose-500 text-white border-rose-600";
            else if (intensity > 40) bgClass = "bg-rose-400 text-white border-rose-500";
            else if (intensity > 15) bgClass = "bg-rose-200 text-rose-900 border-rose-300";
            else bgClass = "bg-rose-50 text-rose-700 border-rose-100";
          }
          
          return (
            <div 
              key={sector.sector}
              className={cn(
                "p-3 rounded-lg border flex flex-col justify-between h-20 transition-all hover:scale-[1.02]",
                bgClass
              )}
            >
              <div className="text-[11px] font-medium leading-tight truncate">
                {sector.sector}
              </div>
              <div className="text-[15px] font-bold tracking-tight">
                {isPositive ? "+" : ""}{formatPercent(sector.change)}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
