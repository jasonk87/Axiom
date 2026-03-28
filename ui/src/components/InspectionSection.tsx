import type { ReactNode } from "react";

type InspectionSectionProps = {
  title: string;
  open: boolean;
  onToggle: () => void;
  countLabel?: string;
  children: ReactNode;
};

export function InspectionSection({ title, open, onToggle, countLabel, children }: InspectionSectionProps) {
  return (
    <section className="inspection-section">
      <button className="inspection-section-toggle" onClick={onToggle}>
        <span>{title}</span>
        <span className="inspection-section-meta">
          {countLabel ? <span>{countLabel}</span> : null}
          <strong>{open ? "Hide" : "Show"}</strong>
        </span>
      </button>
      {open ? <div className="inspection-section-body">{children}</div> : null}
    </section>
  );
}
