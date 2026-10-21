import { CodeIcon, LifeBuoyIcon, LightbulbIcon, ShieldCheckIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { ExternalLink } from '@/components/external-link';
import { ReportBug } from '@/components/report-bug';
import { ISSUES_URL, REPO_URL, SECURITY_URL } from '@shared/utils/contactLinks';
import { SettingsSection, SettingsRow } from './settings-layout';

// Help: where to report problems and find the source. Nothing else.
export function SupportSettings() {
  const { t } = useTranslation();
  const repoLabel = REPO_URL.replace(/^https?:\/\//, '');
  return (
    <SettingsSection icon={LifeBuoyIcon} title={t('donate.title')}>
      <SettingsRow
        id="help-issues"
        title={t('contact.feature_title')}
        description={t('contact.feature_desc')}
      >
        <div className="flex flex-wrap items-center gap-2">
          <ReportBug />
          <ExternalLink href={ISSUES_URL}>
            <LightbulbIcon aria-hidden="true" />
            {t('contact.feature_cta')}
          </ExternalLink>
        </div>
      </SettingsRow>
      <SettingsRow
        id="help-security"
        title={t('contact.security_title')}
        description={t('contact.security_desc')}
      >
        <ExternalLink href={SECURITY_URL}>
          <ShieldCheckIcon aria-hidden="true" />
          {t('contact.security_cta')}
        </ExternalLink>
      </SettingsRow>
      <SettingsRow id="help-source" title={t('modelLicense.sectionSource')}>
        <ExternalLink href={REPO_URL}>
          <CodeIcon aria-hidden="true" />
          {repoLabel}
        </ExternalLink>
      </SettingsRow>
    </SettingsSection>
  );
}
