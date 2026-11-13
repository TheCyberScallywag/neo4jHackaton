import { Component, effect, inject } from '@angular/core';
import { Router, RouterLink, RouterLinkActive } from '@angular/router';
import {
  IonApp,
  IonBadge,
  IonContent,
  IonHeader,
  IonIcon,
  IonItem,
  IonLabel,
  IonList,
  IonListHeader,
  IonMenu,
  IonMenuToggle,
  IonNote,
  IonRouterOutlet,
  IonSelect,
  IonSelectOption,
  IonSplitPane,
  IonTitle,
  IonToolbar,
} from '@ionic/angular';

import { OrgStore } from './core/org.store';
import { SessionService } from './core/session.service';

@Component({
  selector: 'app-root',
  templateUrl: 'app.component.html',
  styleUrls: ['app.component.scss'],
  imports: [
    RouterLink,
    RouterLinkActive,
    IonApp,
    IonBadge,
    IonContent,
    IonHeader,
    IonIcon,
    IonItem,
    IonLabel,
    IonList,
    IonListHeader,
    IonMenu,
    IonMenuToggle,
    IonNote,
    IonRouterOutlet,
    IonSelect,
    IonSelectOption,
    IonSplitPane,
    IonTitle,
    IonToolbar,
  ],
})
export class AppComponent {
  protected readonly session = inject(SessionService);
  protected readonly orgs = inject(OrgStore);
  private readonly router = inject(Router);

  constructor() {
    effect(() => {
      if (this.session.signedIn()) this.orgs.load();
      else this.orgs.clear();
    });
  }

  signOut(): void {
    this.session.signOut();
    void this.router.navigateByUrl('/sign-in');
  }
}
