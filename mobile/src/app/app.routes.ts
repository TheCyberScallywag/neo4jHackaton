import { Routes } from '@angular/router';

import { authGuard } from './core/auth.guard';

export const routes: Routes = [
  {
    path: 'sign-in',
    loadComponent: () => import('./pages/sign-in/sign-in.page').then((m) => m.SignInPage),
  },
  {
    path: 'matches',
    canActivate: [authGuard],
    loadComponent: () => import('./pages/matches/matches.page').then((m) => m.MatchesPage),
  },
  {
    path: 'suggestions',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./pages/submissions/submissions.page').then((m) => m.SubmissionsPage),
  },
  {
    path: 'profile',
    canActivate: [authGuard],
    loadComponent: () => import('./pages/profile/profile.page').then((m) => m.ProfilePage),
  },
  {
    path: 'opportunities',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./pages/opportunities/opportunities.page').then((m) => m.OpportunitiesPage),
  },
  {
    path: 'notifications',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./pages/notifications/notifications.page').then((m) => m.NotificationsPage),
  },
  { path: '', redirectTo: 'matches', pathMatch: 'full' },
  { path: '**', redirectTo: 'matches' },
];
