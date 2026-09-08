/*
 * Browser reminder notifications.
 *
 * Notifications only work while a tab is open, so polling is paused whenever
 * the tab is hidden and resumed when it becomes visible again. Permission is
 * requested from a button click rather than on load, because browsers block
 * or penalise prompts that are not tied to a user gesture.
 */

function reminderNotifier(pollSeconds) {
    return {
        supported: typeof window !== 'undefined' && 'Notification' in window,
        permission: 'default',
        timer: null,
        lastCheck: 0,

        init() {
            if (!this.supported) {
                return;
            }

            this.permission = Notification.permission;

            document.addEventListener('visibilitychange', () => {
                if (document.hidden) {
                    this.stopPolling();
                } else {
                    this.startPolling();
                }
            });

            if (!document.hidden) {
                this.startPolling();
            }
        },

        async requestPermission() {
            if (!this.supported) {
                return;
            }

            try {
                // Safari older than 16 calls back instead of returning a promise.
                const result = await Notification.requestPermission();
                this.permission = result || Notification.permission;
            } catch (error) {
                this.permission = Notification.permission;
            }

            if (this.permission === 'granted') {
                this.startPolling();
            }
        },

        startPolling() {
            // Polling is pointless until permission is granted, and a second
            // timer must never be stacked on top of a running one.
            if (this.timer !== null || this.permission !== 'granted') {
                return;
            }

            this.check();
            this.timer = setInterval(() => this.check(), pollSeconds * 1000);
        },

        stopPolling() {
            if (this.timer !== null) {
                clearInterval(this.timer);
                this.timer = null;
            }
        },

        async check() {
            if (this.permission !== 'granted' || document.hidden) {
                return;
            }

            // Resuming runs a check straight away, so repeatedly switching
            // tabs would otherwise send a request per switch.
            const now = Date.now();
            if (now - this.lastCheck < (pollSeconds * 1000) / 2) {
                return;
            }
            this.lastCheck = now;

            let payload;
            try {
                const response = await fetch('/api/reminders/check', {
                    headers: { 'Accept': 'application/json' },
                    credentials: 'same-origin',
                });

                if (response.status === 401) {
                    // The session ended; there is nothing left to poll for.
                    this.stopPolling();
                    return;
                }

                if (!response.ok) {
                    return;
                }

                payload = await response.json();
            } catch (error) {
                // A failed poll is not worth surfacing; the next one may work.
                return;
            }

            (payload.overruns || []).forEach((item) => {
                this.notify(
                    'Task running long',
                    `"${item.title}" is ${item.overrun_minutes} minute(s) over its estimate.`
                );
            });

            (payload.due_to_start || []).forEach((item) => {
                this.notify(
                    'Scheduled task not started',
                    `"${item.title}" was due to start.`
                );
            });

            (payload.stale || []).forEach((item) => {
                this.notify(
                    'Task still not started',
                    `"${item.title}" has been waiting ${item.age_minutes} minute(s), longer than its estimate.`
                );
            });
        },

        notify(title, body) {
            try {
                const notification = new Notification(title, { body: body });
                notification.onclick = () => {
                    window.focus();
                    notification.close();
                };
            } catch (error) {
                // Some browsers throw when constructing notifications in
                // contexts they consider ineligible; nothing to do but skip.
            }
        },
    };
}
