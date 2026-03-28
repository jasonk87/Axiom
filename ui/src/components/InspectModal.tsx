import type { ReactNode } from "react";

type InspectModalProps = {
  title: string;
  open: boolean;
  onClose: () => void;
  children: ReactNode;
};

export function InspectModal({ title, open, onClose, children }: InspectModalProps) {
  if (!open) {
    return null;
  }

  return (
    <div className="inspect-modal-backdrop" onClick={onClose}>
      <div className="inspect-modal" onClick={(event) => event.stopPropagation()}>
        <div className="inspect-modal-header">
          <strong>{title}</strong>
          <button className="inline-toggle-button" onClick={onClose}>Close</button>
        </div>
        <div className="inspect-modal-body">{children}</div>
      </div>
    </div>
  );
}
