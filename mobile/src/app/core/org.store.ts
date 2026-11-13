import { Injectable, computed, inject, signal } from '@angular/core';

import { ApiService, Me } from './api.service';

/** The signed-in user's organisations and the active one (switching never grants access). */
@Injectable({ providedIn: 'root' })
export class OrgStore {
  private readonly api = inject(ApiService);
  readonly me = signal<Me | null>(null);
  readonly volunteering = computed(() => (this.me()?.organisations ?? []).filter((o) => o.opted_in));
  readonly activeId = computed(() => this.me()?.active_org_id ?? null);

  load(): void {
    this.api.me().subscribe({ next: (me) => this.me.set(me), error: () => this.me.set(null) });
  }

  switchTo(orgId: string): void {
    this.api.setActiveOrg(orgId).subscribe(({ active_org_id }) => {
      const me = this.me();
      if (me) this.me.set({ ...me, active_org_id });
    });
  }

  clear(): void {
    this.me.set(null);
  }
}
