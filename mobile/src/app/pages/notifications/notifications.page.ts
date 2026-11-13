import { Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import {
  IonButton,
  IonButtons,
  IonContent,
  IonHeader,
  IonItem,
  IonLabel,
  IonList,
  IonMenuButton,
  IonTitle,
  IonToolbar,
} from '@ionic/angular';

import { ApiService, Notification } from '../../core/api.service';
import { OrgStore } from '../../core/org.store';

const APP_ROUTES = ['/opportunities', '/profile', '/suggestions', '/matches'];

/** Every onboarding and placement step is announced here (and on the web Notifications page). */
@Component({
  selector: 'app-notifications',
  templateUrl: 'notifications.page.html',
  imports: [RouterLink, IonButton, IonButtons, IonContent, IonHeader, IonItem, IonLabel, IonList, IonMenuButton, IonTitle, IonToolbar],
})
export class NotificationsPage {
  private readonly api = inject(ApiService);
  private readonly orgs = inject(OrgStore);
  readonly items = signal<Notification[]>([]);

  constructor() {
    this.load();
  }

  load(): void {
    this.api.notifications().subscribe((items) => this.items.set(items));
  }

  /** Web-only links (e.g. a project page) are shown as text; app screens are tappable. */
  appLink(n: Notification): string | null {
    return APP_ROUTES.includes(n.link) ? (n.link === '/submissions' ? '/suggestions' : n.link) : null;
  }

  readAll(): void {
    this.api.markAllRead().subscribe(() => {
      this.load();
      this.orgs.load();
    });
  }
}
