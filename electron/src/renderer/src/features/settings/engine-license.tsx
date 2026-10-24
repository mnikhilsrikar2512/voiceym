import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { LoaderCircleIcon, ScaleIcon } from 'lucide-react';
import { toast } from 'sonner';
import { ExternalLink } from '@/components/external-link';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { apiJson, describeError } from '@/lib/api/client';
import { queryKeys } from '@/lib/query';

// Engines that need a local licence acceptance before first use. None of the
// shipped commercial-use engines do; an entry here adds one.
type LicensedEngine = string;

const LICENSES: Record<
  LicensedEngine,
  {
    title: string;
    intro: string;
    sections: { heading: string; description: string; link: string; url: string }[];
    footer: string;
    acceptedToast: string;
  }
> = {};

export function licensedEngine(
  id: string,
  required?: boolean,
  accepted?: boolean,
): LicensedEngine | null {
  if (!(id in LICENSES) || required !== true || accepted === true) return null;
  return id;
}

export function EngineLicense({ id, name }: { id: LicensedEngine; name: string }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const license = LICENSES[id];

  const accept = async () => {
    setBusy(true);
    setFailed(null);
    try {
      await apiJson('/api/settings/license', {
        method: 'POST',
        body: JSON.stringify({ engine_id: id, accepted: true }),
      });
      await client.invalidateQueries({ queryKey: queryKeys.engines });
      toast.success(t(license.acceptedToast));
      setOpen(false);
    } catch (error) {
      const message = describeError(error);
      setFailed(message);
      toast.error(t('license.accept_error', { message }));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Button
        size="sm"
        variant="outline"
        onClick={() => setOpen(true)}
        aria-label={`${t('engines.acceptLicense')}: ${name}`}
      >
        <ScaleIcon />
        {t('engines.acceptLicense')}
      </Button>
      <Dialog
        open={open}
        onOpenChange={(nextOpen) => {
          if (!busy) setOpen(nextOpen);
        }}
      >
        <DialogContent showCloseButton={!busy} className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <ScaleIcon className="size-4 text-primary" />
              {t(license.title)}
            </DialogTitle>
            <DialogDescription>{t(license.intro)}</DialogDescription>
          </DialogHeader>

          <div className="grid gap-2.5">
            {license.sections.map((section) => (
              <section
                key={section.heading}
                className="rounded-lg border border-border/60 bg-muted/25 p-3.5"
              >
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-foreground/80">
                  {t(section.heading)}
                </h3>
                <p className="mb-2 text-sm leading-5 text-muted-foreground">
                  {t(section.description)}
                </p>
                <ExternalLink href={section.url}>{t(section.link)}</ExternalLink>
              </section>
            ))}
          </div>

          <p className="text-xs leading-5 text-muted-foreground">{t(license.footer)}</p>
          {failed && (
            <p role="alert" className="text-sm text-destructive">
              {t('license.accept_error', { message: failed })}
            </p>
          )}
          <DialogFooter>
            <Button variant="ghost" disabled={busy} onClick={() => setOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button disabled={busy} onClick={() => void accept()}>
              {busy && <LoaderCircleIcon className="animate-spin" />}
              {t(busy ? 'license.saving' : 'license.accept')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
