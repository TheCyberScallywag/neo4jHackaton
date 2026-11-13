import { Component, effect, inject, signal } from '@angular/core';
import {
  IonBadge,
  IonButtons,
  IonCard,
  IonCardContent,
  IonCardHeader,
  IonCardSubtitle,
  IonCardTitle,
  IonChip,
  IonContent,
  IonHeader,
  IonItem,
  IonMenuButton,
  IonText,
  IonTitle,
  IonToggle,
  IonToolbar,
} from '@ionic/angular';

import { ApiService, Matches } from '../../core/api.service';
import { OrgStore } from '../../core/org.store';

/** Explainable matches for the active organisation, decided by the server's Relate query. */
@Component({
  selector: 'app-matches',
  templateUrl: 'matches.page.html',
  imports: [
    IonBadge,
    IonButtons,
    IonCard,
    IonCardContent,
    IonCardHeader,
    IonCardSubtitle,
    IonCardTitle,
    IonChip,
    IonContent,
    IonHeader,
    IonItem,
    IonMenuButton,
    IonText,
    IonTitle,
    IonToggle,
    IonToolbar,
  ],
})
export class MatchesPage {
  private readonly api = inject(ApiService);
  protected readonly orgs = inject(OrgStore);
  readonly preferredOnly = signal(false);
  readonly result = signal<Matches | null>(null);
  readonly error = signal<string | null>(null);

  constructor() {
    effect(() => {
      const org = this.orgs.activeId();
      const preferred = this.preferredOnly();
      if (org) this.load(org, preferred);
    });
  }

  load(orgId: string, preferredOnly: boolean): void {
    this.error.set(null);
    this.api.matches(orgId, preferredOnly).subscribe({
      next: (r) => this.result.set(r),
      error: (e) => this.error.set(e.status === 403 ? 'Join this organisation as a volunteer to see matches.' : 'Could not load matches.'),
    });
  }
}
