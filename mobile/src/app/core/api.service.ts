import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';
import type { Matches, Me, Notification, Placement, Profile, ProfileUpdate, Submission } from './api.types';

export type * from './api.types';

/** The app's only way to reach data: the web app's bearer-token JSON API under /api/v1. */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);
  private readonly base = `${environment.apiUrl}/v1`;

  signIn(email: string, password: string): Observable<{ token: string }> {
    return this.http.post<{ token: string }>(`${this.base}/session`, { email, password });
  }

  signOut(): Observable<void> {
    return this.http.delete<void>(`${this.base}/session`);
  }

  me(): Observable<Me> {
    return this.http.get<Me>(`${this.base}/me`);
  }

  setActiveOrg(orgId: string): Observable<{ active_org_id: string }> {
    return this.http.put<{ active_org_id: string }>(`${this.base}/me/active-org`, { org_id: orgId });
  }

  matches(orgId: string, preferredOnly = false): Observable<Matches> {
    return this.http.get<Matches>(`${this.base}/orgs/${encodeURIComponent(orgId)}/matches`, {
      params: { preferred_only: preferredOnly },
    });
  }

  profile(): Observable<Profile> {
    return this.http.get<Profile>(`${this.base}/profile`);
  }

  saveProfile(update: ProfileUpdate): Observable<Profile> {
    return this.http.put<Profile>(`${this.base}/profile`, update);
  }

  submitForVetting(): Observable<{ status: string }> {
    return this.http.post<{ status: string }>(`${this.base}/me/vetting`, {});
  }

  notifications(): Observable<Notification[]> {
    return this.http.get<Notification[]>(`${this.base}/notifications`);
  }

  markAllRead(): Observable<{ read: number }> {
    return this.http.post<{ read: number }>(`${this.base}/notifications/read-all`, {});
  }

  placements(): Observable<Placement[]> {
    return this.http.get<Placement[]>(`${this.base}/placements`);
  }

  answer(projectId: string, answer: 'yes' | 'no', version: number): Observable<{ state: string }> {
    return this.http.post<{ state: string }>(`${this.base}/placements/${encodeURIComponent(projectId)}/answer`, {
      answer,
      version,
    });
  }

  leaveTeam(projectId: string): Observable<{ state: string }> {
    return this.http.post<{ state: string }>(`${this.base}/placements/${encodeURIComponent(projectId)}/leave`, {});
  }

  submissions(): Observable<Submission[]> {
    return this.http.get<Submission[]>(`${this.base}/submissions`);
  }
}
