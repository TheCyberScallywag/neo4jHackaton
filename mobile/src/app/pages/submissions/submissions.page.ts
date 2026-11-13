import { Component, inject, signal } from '@angular/core';
import {
  IonButtons,
  IonContent,
  IonHeader,
  IonItem,
  IonLabel,
  IonList,
  IonMenuButton,
  IonNote,
  IonTitle,
  IonToolbar,
} from '@ionic/angular';

import { ApiService, Submission } from '../../core/api.service';

const LABELS: Record<Submission['state'], string> = {
  submitted: 'In review',
  changes_requested: 'Scoping: more detail requested',
  rejected: 'Rejected',
  approved: 'Approved (activating…)',
  published: 'Active',
  withdrawn: 'Withdrawn',
};

/** Track suggestions; feedback and revision happen in the web app. */
@Component({
  selector: 'app-submissions',
  templateUrl: 'submissions.page.html',
  imports: [IonButtons, IonContent, IonHeader, IonItem, IonLabel, IonList, IonMenuButton, IonNote, IonTitle, IonToolbar],
})
export class SubmissionsPage {
  readonly items = signal<Submission[]>([]);
  readonly label = (s: Submission['state']) => LABELS[s];

  constructor() {
    inject(ApiService)
      .submissions()
      .subscribe((items) => this.items.set(items));
  }
}
