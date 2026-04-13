import type { ReactNode } from "react";
import { ChevronDown, ChevronRight, Check } from "lucide-react";

export type SessionStepStatus = "pending" | "running" | "complete" | "warning";

type SessionStepCardProps = {
  id: string;
  title: string;
  summary: string;
  status: SessionStepStatus;
  expanded: boolean;
  onToggle: (id: string) => void;
  actionLabel?: string;
  onAction?: () => void;
  children?: ReactNode;
};

export function SessionStepCard({
  id,
  title,
  summary,
  status,
  expanded,
  onToggle,
  actionLabel,
  onAction,
  children,
}: SessionStepCardProps) {
  return (
    <article className={`session-step-card status-${status}`}>
      <div className="session-step-rail">
        <span className={`session-step-icon status-${status}`}>
          {status === "complete" ? <Check size={16} color="white" /> : <span className="session-step-icon-inner" />}
        </span>
      </div>
      <div className="session-step-main">
        <div className="session-step-header">
          <div className="session-step-copy">
            <h3>{title}</h3>
            <p>{summary}</p>
          </div>
          <div className="session-step-actions">
            {actionLabel && onAction ? (
              <button className="inline-action-button" onClick={onAction}>
                {actionLabel}
              </button>
            ) : null}
            {children ? (
              <button className="inline-toggle-button" onClick={() => onToggle(id)} title={expanded ? "Collapse" : "Expand"}>
                {expanded ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
              </button>
            ) : null}
          </div>
        </div>
        <div className={`session-step-details-wrapper ${expanded ? 'expanded' : ''}`}>
          <div className="session-step-details">
            <div style={{ paddingTop: 14 }}>{children}</div>
          </div>
        </div>
      </div>
    </article>
  );
}
