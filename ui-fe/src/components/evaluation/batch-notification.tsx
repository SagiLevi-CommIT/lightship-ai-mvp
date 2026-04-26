'use client';

import type { NotificationState } from '@/components/evaluation/flow-types';

type BatchNotificationProps = {
  message: string | null;
  permission: NotificationState;
  onRequestPermission: () => void;
  onDismiss: () => void;
};

export default function BatchNotification({
  message,
  permission,
  onRequestPermission,
  onDismiss,
}: BatchNotificationProps) {
  if (!message) {
    return null;
  }

  return (
    <div className="rounded-[28px] border border-cyan-500/20 bg-slate-950/78 p-5 shadow-[0_20px_50px_rgba(2,8,20,0.35)]">
      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-300">Notification</p>
          <p className="mt-2 text-sm font-semibold text-white">{message}</p>
        </div>

        <div className="flex flex-wrap gap-3">
          {permission !== 'granted' && permission !== 'unsupported' ? (
            <button
              type="button"
              onClick={onRequestPermission}
              className="rounded-full border border-cyan-400/30 bg-cyan-500/10 px-4 py-2 text-sm font-semibold text-cyan-200 shadow-sm transition hover:bg-cyan-500/20"
            >
              Enable browser notification
            </button>
          ) : null}

          <button
            type="button"
            onClick={onDismiss}
            className="rounded-full border border-white/10 bg-white/5 px-4 py-2 text-sm font-semibold text-slate-200 shadow-sm transition hover:bg-white/10"
          >
            Dismiss
          </button>
        </div>
      </div>
    </div>
  );
}
