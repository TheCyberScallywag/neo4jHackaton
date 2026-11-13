import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { provideIonicAngular } from '@ionic/angular';

import { SignInPage } from './sign-in.page';

function setup() {
  localStorage.clear();
  TestBed.configureTestingModule({
    imports: [SignInPage],
    providers: [provideIonicAngular(), provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
  });
  const fixture = TestBed.createComponent(SignInPage);
  fixture.detectChanges();
  const navigate = vi.spyOn(TestBed.inject(Router), 'navigateByUrl').mockResolvedValue(true);
  return { page: fixture.componentInstance, http: TestBed.inject(HttpTestingController), navigate };
}

describe('SignInPage', () => {
  it('signs in and goes to matches', () => {
    const { page, http, navigate } = setup();
    page.form.setValue({ email: 'asha@demo.invalid', password: 'demo-password-1' });
    page.submit();
    http.expectOne('/api/v1/session').flush({ token: 'tok' });
    expect(navigate).toHaveBeenCalledWith('/matches');
  });

  it('shows a clear error for wrong credentials', () => {
    const { page, http } = setup();
    page.form.setValue({ email: 'asha@demo.invalid', password: 'nope' });
    page.submit();
    http.expectOne('/api/v1/session').flush({}, { status: 401, statusText: '' });
    expect(page.error()).toBe('Wrong email or password');
  });

  it('does not submit an invalid form', () => {
    const { page, http } = setup();
    page.submit();
    http.expectNone('/api/v1/session');
  });
});
