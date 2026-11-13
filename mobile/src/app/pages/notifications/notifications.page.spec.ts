import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { provideIonicAngular } from '@ionic/angular';

import { ME, NOTIFICATIONS } from '../../testing/fixtures';
import { NotificationsPage } from './notifications.page';

describe('NotificationsPage', () => {
  it('lists notifications, newest first, and marks them read', () => {
    TestBed.configureTestingModule({
      imports: [NotificationsPage],
      providers: [provideIonicAngular(), provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    const fixture = TestBed.createComponent(NotificationsPage);
    const http = TestBed.inject(HttpTestingController);
    http.expectOne('/api/v1/notifications').flush(NOTIFICATIONS);
    fixture.detectChanges();
    const text = fixture.nativeElement.textContent;
    expect(text.indexOf('on the team')).toBeLessThan(text.indexOf('approved and active'));
    expect(fixture.componentInstance.appLink(NOTIFICATIONS[0])).toBe('/opportunities');
    fixture.componentInstance.readAll();
    http.expectOne('/api/v1/notifications/read-all').flush({ read: 1 });
    http.expectOne('/api/v1/notifications').flush(NOTIFICATIONS);
    http.expectOne('/api/v1/me').flush(ME);
  });
});
