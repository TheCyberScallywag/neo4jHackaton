import { Injectable, InjectionToken, computed, inject, signal } from '@angular/core';
import { Observable, map, tap } from 'rxjs';

import { ApiService } from './api.service';
import meta from './meta.json';

/**
 * Where the opaque session token is kept. localStorage for the web build; a native build can
 * provide a secure-storage plugin here without touching any caller.
 */
export interface TokenStore {
  read(): string | null;
  write(token: string): void;
  clear(): void;
}

const KEY = `${meta.slug}.token`;

export const TOKEN_STORE = new InjectionToken<TokenStore>('TOKEN_STORE', {
  providedIn: 'root',
  factory: (): TokenStore => ({
    read: () => {
      try {
        return localStorage.getItem(KEY);
      } catch {
        return null;
      }
    },
    write: (token) => {
      try {
        localStorage.setItem(KEY, token);
      } catch {
        // not persisted; lasts until reload
      }
    },
    clear: () => {
      try {
        localStorage.removeItem(KEY);
      } catch {
        // nothing stored
      }
    },
  }),
});

/** Bearer session: the server can revoke it at any time; a 401 signs the app out. */
@Injectable({ providedIn: 'root' })
export class SessionService {
  private readonly api = inject(ApiService);
  private readonly store = inject(TOKEN_STORE);

  readonly token = signal<string | null>(this.store.read());
  readonly signedIn = computed(() => this.token() !== null);

  signIn(email: string, password: string): Observable<void> {
    return this.api.signIn(email, password).pipe(
      tap(({ token }) => {
        this.token.set(token);
        this.store.write(token);
      }),
      map(() => undefined),
    );
  }

  /** Revoke on the server when possible, and always forget locally. */
  signOut(): void {
    if (this.token()) this.api.signOut().subscribe({ error: () => undefined });
    this.forget();
  }

  forget(): void {
    this.token.set(null);
    this.store.clear();
  }
}
