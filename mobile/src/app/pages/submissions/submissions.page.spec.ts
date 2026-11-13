import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideIonicAngular } from '@ionic/angular';

import { SUBMISSIONS } from '../../testing/fixtures';
import { SubmissionsPage } from './submissions.page';

describe('SubmissionsPage', () => {
  it('lists suggestions with their review state', () => {
    TestBed.configureTestingModule({
      imports: [SubmissionsPage],
      providers: [provideIonicAngular(), provideHttpClient(), provideHttpClientTesting()],
    });
    const fixture = TestBed.createComponent(SubmissionsPage);
    TestBed.inject(HttpTestingController).expectOne('/api/v1/submissions').flush(SUBMISSIONS);
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Outreach Data Audit');
    expect(fixture.nativeElement.textContent).toContain('In review');
  });
});
