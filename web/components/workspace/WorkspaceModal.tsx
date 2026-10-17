"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";
import { X } from "lucide-react";
import styles from "./workspace.module.css";

export default function WorkspaceModal({ open, title, children, onClose, className = "" }: {
  open: boolean; title: string; children: ReactNode; onClose: () => void; className?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    if (open && dialog && !dialog.open) {
      dialog.showModal();
      const previous = document.documentElement.style.overflow;
      document.documentElement.style.overflow = "hidden";
      return () => { if (dialog.open) dialog.close(); document.documentElement.style.overflow = previous; };
    }
    if (!open && dialog?.open) dialog.close();
  }, [open]);
  return <dialog ref={ref} className={`${styles.modal} ${className}`} aria-labelledby={titleId} onCancel={onClose} onClose={onClose}
    onClick={(event) => { if (event.target !== event.currentTarget) return; const rect = event.currentTarget.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) onClose(); }}>
    <div className={`${styles.modalHeader} ${styles.noPrint}`}><span className={styles.eyebrow} id={titleId}>{title}</span><button className={styles.iconButton} onClick={onClose} aria-label={`Close ${title}`}><X size={19} /></button></div>
    {children}
  </dialog>;
}
