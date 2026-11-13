import { HttpErrorResponse } from '@angular/common/http';
import { Component, inject, signal } from '@angular/core';
import {
  IonButton,
  IonButtons,
  IonCard,
  IonCardContent,
  IonCardHeader,
  IonCardSubtitle,
  IonCardTitle,
  IonChip,
  IonContent,
  IonHeader,
  IonMenuButton,
  IonNote,
  IonText,
  IonTitle,
  IonToolbar,
} from '@ionic/angular';

import { ApiService, Placement } from '../../core/api.service';
import { OrgStore } from '../../core/org.store';

/** Onboarding step 2: projects Relate found you eligible for. You and the lead both say yes. */
@Component({
  selector: 'app-opportunities',
  templateUrl: 'opportunities.page.html',
  imports: [
    IonButton,
    IonButtons,
    IonCard,
    IonCardContent,
    IonCardHeader,
    IonCardSubtitle,
    IonCardTitle,
    IonChip,
    IonContent,
    IonHeader,
    IonMenuButton,
    IonNote,
    IonText,
    IonTitle,
    IonToolbar,
  ],
})
export class OpportunitiesPage {
  private readonly api = inject(ApiService);
  protected readonly orgs = inject(OrgStore);
  readonly items = signal<Placement[]>([]);
  readonly message = signal<string | null>(null);

  constructor() {
    this.load();
  }

  load(): void {
    this.api.placements().subscribe((items) => this.items.set(items));
  }

  answer(p: Placement, answer: 'yes' | 'no'): void {
    this.api.answer(p.project_id, answer, p.version).subscribe({
      next: ({ state }) => {
        this.message.set(
          state === 'team' ? 'You are on the team!' : answer === 'yes' ? 'Waiting for the project lead.' : 'Declined.',
        );
        this.load();
        this.orgs.load();
      },
      error: (err: HttpErrorResponse) => {
        this.message.set(err.status === 409 ? 'This offer changed; reloaded.' : 'Could not record your answer.');
        this.load();
      },
    });
  }

  leave(p: Placement): void {
    this.api.leaveTeam(p.project_id).subscribe(() => {
      this.message.set('You left the team.');
      this.load();
    });
  }
}
