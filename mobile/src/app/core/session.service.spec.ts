import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import meta from './meta.json';
import { SessionService } from './session.service';

const KEY = `${meta.slug}.token`;

function setup() {
  TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
  return { session: TestBed.inject(SessionService), http: TestBed.inject(HttpTestingController) };
}

describe('SessionService', () => {
  beforeEach(() => localStorage.clear());

  it('signs in and keeps the token across restarts', () => {
    const { session, http } = setup();
    session.signIn('asha@demo.invalid', 'pw').subscribe();
    const req = http.expectOne('/api/v1/session');
    expect(req.request.body).toEqual({ email: 'asha@demo.invalid', password: 'pw' });
    req.flush({ token: 'tok' });
    expect(session.token()).toBe('tok');
    TestBed.resetTestingModule();
    expect(setup().session.signedIn()).toBe(true);
  });

  it('revokes on the server and forgets locally on sign-out', () => {
    localStorage.setItem(KEY, 'tok');
    const { session, http } = setup();
    session.signOut();
    expect(http.expectOne('/api/v1/session').request.method).toBe('DELETE');
    expect(session.signedIn()).toBe(false);
    expect(localStorage.getItem(KEY)).toBeNull();
  });
});
