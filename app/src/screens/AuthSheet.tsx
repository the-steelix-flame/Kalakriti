import React, { useEffect, useRef, useState } from 'react';
import {
  View, Text, Modal, ScrollView, Pressable, TextInput, Switch,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Card, Btn, Pill, Field, Divider } from '../ui';
import { C, GRAD, S, T, R, shadow } from '../theme';
import { Shield, Check, Warning, Arrow, Back } from '../icons';
import * as api from '../lib/api';
import * as session from '../lib/session';
import { useI18n } from '../i18n';

/**
 * The login + profile flow, presented as a sheet over whatever the artisan was
 * doing. It is a modal on purpose: the listing underneath stays mounted, so
 * finishing here returns to the exact same scroll position with every field intact.
 *
 * Stages appear only as they become necessary (progressive profiling):
 *   phone -> otp -> name -> address -> [extra fields this marketplace needs]
 * Nothing is asked before it is actually required by something the artisan chose.
 */

type Stage = 'phone' | 'otp' | 'profile' | 'address' | 'extra' | 'done';


export default function AuthSheet({
  visible, onClose, onAuthed, requiredFor, reason,
}: {
  visible: boolean;
  onClose: () => void;
  onAuthed: (a: api.Artisan) => void;
  requiredFor?: string[];          // channels the artisan is trying to publish to
  reason?: string;                 // why this opened
}) {
  const { t, lang, setLang } = useI18n();
  const [stage, setStage] = useState<Stage>('phone');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [notice, setNotice] = useState('');

  const [phone, setPhone] = useState('');
  const [challengeId, setChallengeId] = useState('');
  const [code, setCode] = useState('');
  const [devCode, setDevCode] = useState('');
  const [cooldown, setCooldown] = useState(0);

  const [artisan, setArtisan] = useState<api.Artisan | null>(null);
  const [readiness, setReadiness] = useState<api.Readiness[]>([]);

  const [fullName, setFullName] = useState('');
  const [businessName, setBusinessName] = useState('');

  const [line1, setLine1] = useState('');
  const [line2, setLine2] = useState('');
  const [city, setCity] = useState('');
  const [stateName, setStateName] = useState('');
  const [pincode, setPincode] = useState('');
  const [sameReturn, setSameReturn] = useState(true);

  const [gstin, setGstin] = useState('');
  const [pan, setPan] = useState('');
  const [bankAccount, setBankAccount] = useState('');
  const [bankIfsc, setBankIfsc] = useState('');

  useEffect(() => {
    if (!visible) return;
    setErr(''); setNotice('');
    // Already logged in? Jump straight to whatever is still missing.
    api.me().then(({ artisan: a, marketplaceReadiness }) => {
      setArtisan(a); setReadiness(marketplaceReadiness);
      setFullName(a.fullName); setBusinessName(a.businessName);
      setGstin(a.gstin); setPan(a.pan);
      setBankAccount(a.bankAccount); setBankIfsc(a.bankIfsc);
      const pick = a.addresses.find((x) => x.kind === 'pickup');
      if (pick) {
        setLine1(pick.line1); setLine2(pick.line2); setCity(pick.city);
        setStateName(pick.state); setPincode(pick.pincode);
      }
      setStage(nextStage(a, marketplaceReadiness));
    }).catch(() => setStage('phone'));
  }, [visible]);

  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  /** The needed set is the union of what the chosen channels still require. */
  function needed(rd: api.Readiness[]): string[] {
    const chans = requiredFor?.length ? requiredFor : ['storefront'];
    const out = new Set<string>();
    rd.filter((r) => chans.includes(r.channel))
      .forEach((r) => r.missing.forEach((m) => out.add(m)));
    return [...out];
  }

  function nextStage(a: api.Artisan, rd: api.Readiness[]): Stage {
    const miss = needed(rd);
    if (!a.phoneVerified) return 'otp';
    if (miss.includes('full_name')) return 'profile';
    if (miss.some((m) => m.endsWith('_address'))) return 'address';
    if (miss.length) return 'extra';
    return 'done';
  }

  async function sendOtp() {
    setBusy(true); setErr(''); setNotice('');
    try {
      const r = await api.requestOtp(phone);
      if (r.challengeId) setChallengeId(r.challengeId);
      if (r.devCode) { setDevCode(r.devCode); setNotice(r.devNotice || ''); }
      if (!r.ok && r.error === 'sms_not_configured') {
        setErr(r.message || 'SMS not configured');
      } else if (!r.ok) {
        setErr(r.message || 'Could not send the code');
        if (r.retryAfter) setCooldown(r.retryAfter);
      }
      if (r.challengeId) { setStage('otp'); setCooldown(45); }
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally { setBusy(false); }
  }

  async function verify() {
    setBusy(true); setErr('');
    try {
      const r = await api.verifyOtp(challengeId, code, session.guestToken());
      session.setToken(r.token);
      setArtisan(r.artisan);
      // The language chosen on this device wins, and is pushed up to the account so
      // it follows the artisan to another phone. Taking the account's language
      // instead - which this used to do - meant signing in silently changed the
      // interface out from under somebody who had just picked Gujarati, because the
      // account still held the default. The account's language is only adopted when
      // the device has no choice of its own recorded.
      const chosen = session.getLang();
      if (chosen) {
        if (r.artisan.language !== chosen) {
          api.updateProfile({ language: chosen }).catch(() => { /* queued elsewhere */ });
        }
      } else if (r.artisan.language) {
        setLang(r.artisan.language as any);
      }
      const rd = await api.readiness();
      setReadiness(rd.readiness);
      setFullName(r.artisan.fullName);
      if (r.claimedDrafts > 0) {
        setNotice(t('auth.workKept', { n: r.claimedDrafts }));
      }
      const nxt = nextStage(r.artisan, rd.readiness);
      setStage(nxt);
      if (nxt === 'done') onAuthed(r.artisan);
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally { setBusy(false); }
  }

  async function saveProfile() {
    setBusy(true); setErr('');
    try {
      const r = await api.updateProfile({ fullName, businessName });
      setArtisan(r.artisan); setReadiness(r.marketplaceReadiness);
      const nxt = nextStage(r.artisan, r.marketplaceReadiness);
      setStage(nxt);
      if (nxt === 'done') onAuthed(r.artisan);
    } catch (e: any) { setErr(e?.message || String(e)); }
    finally { setBusy(false); }
  }

  async function saveAddress() {
    setBusy(true); setErr('');
    try {
      const r = await api.saveAddress({
        kind: 'pickup', contactName: fullName, contactPhone: artisan?.phone || phone,
        line1, line2, city, state: stateName, pincode, country: 'India',
        copyToReturn: sameReturn,
      });
      let art = r.artisan; let rd = r.marketplaceReadiness;
      // ONDC and GeM want a registered business address; reuse pickup unless the
      // artisan later edits it separately.
      if (needed(rd).includes('business_address')) {
        const r2 = await api.saveAddress({
          kind: 'business', contactName: fullName,
          contactPhone: artisan?.phone || phone,
          line1, line2, city, state: stateName, pincode, country: 'India',
        });
        art = r2.artisan; rd = r2.marketplaceReadiness;
      }
      setArtisan(art); setReadiness(rd);
      const nxt = nextStage(art, rd);
      setStage(nxt);
      if (nxt === 'done') onAuthed(art);
    } catch (e: any) { setErr(e?.message || String(e)); }
    finally { setBusy(false); }
  }

  async function saveExtra() {
    setBusy(true); setErr('');
    try {
      const r = await api.updateProfile({ gstin, pan, bankAccount, bankIfsc });
      setArtisan(r.artisan); setReadiness(r.marketplaceReadiness);
      const nxt = nextStage(r.artisan, r.marketplaceReadiness);
      setStage(nxt);
      if (nxt === 'done') onAuthed(r.artisan);
    } catch (e: any) { setErr(e?.message || String(e)); }
    finally { setBusy(false); }
  }

  const miss = needed(readiness);

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View style={{ flex: 1, backgroundColor: 'rgba(23,19,49,0.55)',
                     justifyContent: 'flex-end' }}>
        <View style={{ backgroundColor: C.bg, borderTopLeftRadius: R.xxl,
                       borderTopRightRadius: R.xxl, maxHeight: '94%' }}>
          <LinearGradient colors={GRAD.indigo} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }}
                          style={{ padding: S.lg, borderTopLeftRadius: R.xxl,
                                   borderTopRightRadius: R.xxl }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
              <Pressable onPress={onClose} hitSlop={12} accessibilityLabel={t('common.close')}
                         style={{ width: 40, height: 40, borderRadius: R.pill,
                                  alignItems: 'center', justifyContent: 'center',
                                  backgroundColor: 'rgba(255,255,255,0.14)' }}>
                <Back color={C.white} size={20} />
              </Pressable>
              <View style={{ flex: 1 }}>
                <Text style={[T.title, { color: C.white }]}>
                  {stage === 'phone' ? t('auth.signIn')
                    : stage === 'otp' ? t('auth.verifyNumber')
                    : stage === 'profile' ? t('auth.yourName')
                    : stage === 'address' ? t('auth.address')
                    : stage === 'extra' ? t('auth.moreInfo')
                    : t('create.ready')}
                </Text>
                <Text style={[T.label, { color: 'rgba(255,255,255,0.72)' }]}>{t('auth.workSafe')}</Text>
              </View>
            </View>
          </LinearGradient>

          <ScrollView contentContainerStyle={{ padding: S.lg, gap: S.md, paddingBottom: 40 }}>
            {reason ? (
              <Card tone="indigo">
                <View style={{ flexDirection: 'row', gap: S.sm, alignItems: 'flex-start' }}>
                  <Shield color={C.indigo} size={20} />
                  <Text style={[T.bodySoft, { flex: 1, color: C.indigo }]}>{reason}</Text>
                </View>
              </Card>
            ) : null}

            {notice ? (
              <Card tone="money">
                <Text style={T.bodySoft}>{notice}</Text>
              </Card>
            ) : null}
            {err ? (
              <Card tone="danger">
                <View style={{ flexDirection: 'row', gap: S.sm, alignItems: 'center' }}>
                  <Warning color={C.danger} size={18} />
                  <Text style={[T.bodySoft, { flex: 1 }]}>{err}</Text>
                </View>
              </Card>
            ) : null}

            {stage === 'phone' ? (
              <>
                <Card>
                  <Field label={t('auth.mobile')} value={phone} onChange={setPhone}
                         numeric suffix="+91" placeholder={t('auth.tenDigits')} />
                  <Text style={[T.micro, { fontSize: 12 }]}>{t('auth.whyPhone')}</Text>
                </Card>
                <Btn label={t('auth.sendCode')} tone="money" large busy={busy}
                     disabled={phone.replace(/\D/g, '').length !== 10}
                     onPress={sendOtp} />
              </>
            ) : null}

            {stage === 'otp' ? (
              <>
                <Card>
                  <Text style={T.label}>{t('auth.codeSentTo', { phone })}</Text>
                  <TextInput
                    value={code} onChangeText={setCode} keyboardType="numeric"
                    maxLength={6} placeholder={t('auth.sixDigits')} placeholderTextColor={C.inkSoft}
                    style={{
                      borderWidth: 1.5, borderColor: C.line, borderRadius: R.md,
                      paddingVertical: 16, paddingHorizontal: S.md, fontSize: 28,
                      letterSpacing: 10, textAlign: 'center', color: C.ink,
                      fontFamily: 'Mukta_700Bold', backgroundColor: C.surface,
                      outlineStyle: 'none',
                    } as any}
                  />
                  {devCode ? (
                    <Card tone="warn">
                      <Pill text={t('auth.smsNotSetUp')} tone="warn" />
                      <Text style={[T.title, { letterSpacing: 6 }]}>{devCode}</Text>
                      <Text style={[T.micro, { fontSize: 12 }]}>{t('auth.devEchoNote')}</Text>
                    </Card>
                  ) : null}
                  <Pressable disabled={cooldown > 0} onPress={sendOtp}>
                    <Text style={[T.bodySoft, { color: cooldown > 0 ? C.inkSoft : C.primary }]}>
                      {cooldown > 0 ? t('auth.resendIn', { n: cooldown }) : t('auth.resend')}
                    </Text>
                  </Pressable>
                </Card>
                <Btn label={t('auth.confirm')} tone="money" large busy={busy}
                     disabled={code.length !== 6} onPress={verify} />
              </>
            ) : null}

            {stage === 'profile' ? (
              <>
                <Card>
                  <Field label={t('auth.fullName')} value={fullName} onChange={setFullName}
                         placeholder={t('auth.namePlaceholder')} />
                  <Text style={[T.micro, { fontSize: 12 }]}>{t('auth.nameNote')}</Text>
                  <Divider />
                  <Field label={t('auth.businessName')}
                         value={businessName} onChange={setBusinessName} />
                </Card>
                <Btn label={t('common.continue')} tone="money" large busy={busy}
                     disabled={fullName.trim().length < 2} onPress={saveProfile} />
              </>
            ) : null}

            {stage === 'address' ? (
              <>
                <Card tone="soft">
                  <Text style={T.bodySoft}>{t('auth.pickupNote')}</Text>
                </Card>
                <Card>
                  <Field label={t('auth.line1')} value={line1} onChange={setLine1}
                         placeholder={t('auth.line1')} />
                  <Field label={t('auth.line2')} value={line2} onChange={setLine2}
                         placeholder={t('auth.line2')} />
                  <Field label={t('auth.city')} value={city} onChange={setCity}
                         placeholder={t('auth.city')} />
                  <Field label={t('auth.state')} value={stateName} onChange={setStateName}
                         placeholder={t('auth.state')} />
                  <Field label={t('auth.pincode')} value={pincode} onChange={setPincode} numeric
                         placeholder={t('auth.sixDigits')} />
                  <Divider />
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: S.md }}>
                    <Switch value={sameReturn} onValueChange={setSameReturn}
                            trackColor={{ true: C.money, false: C.lineStrong }} />
                    <Text style={[T.bodySoft, { flex: 1 }]}>{t('auth.sameReturn')}</Text>
                  </View>
                </Card>
                <Btn label={t('auth.saveAddress')} tone="money" large busy={busy}
                     disabled={!(line1 && city && stateName && pincode.length === 6)}
                     onPress={saveAddress} />
              </>
            ) : null}

            {stage === 'extra' ? (
              <>
                <Card tone="warn">
                  <Text style={T.bodySoft}>{t('auth.needForChannels')}</Text>
                  <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
                    {miss.map((m) => (
                      <Pill key={m} text={t(`field.${m}`)} tone="warn" />
                    ))}
                  </View>
                </Card>
                <Card>
                  {miss.includes('pan') ? (
                    <Field label={t('auth.pan')} value={pan} onChange={setPan}
                           placeholder="ABCDE1234F" />
                  ) : null}
                  {miss.includes('gstin') ? (
                    <Field label={t('auth.gstin')} value={gstin} onChange={setGstin} />
                  ) : null}
                  {miss.includes('bank_account') ? (
                    <>
                      <Field label={t('auth.bankAccount')} value={bankAccount}
                             onChange={setBankAccount} numeric />
                      <Field label={t('auth.bankIfsc')} value={bankIfsc} onChange={setBankIfsc} />
                    </>
                  ) : null}
                </Card>
                <Btn label={t('common.save')} tone="money" large busy={busy} onPress={saveExtra} />
                <Btn label={t('auth.skipExtra')} tone="ghost"
                     onPress={() => { if (artisan) onAuthed(artisan); }} />
              </>
            ) : null}

            {stage === 'done' ? (
              <>
                <Card tone="money" style={{ alignItems: 'center', gap: S.sm,
                                            paddingVertical: S.xl }}>
                  <View style={{ width: 64, height: 64, borderRadius: R.pill,
                                 backgroundColor: C.money, alignItems: 'center',
                                 justifyContent: 'center' }}>
                    <Check color={C.white} size={34} />
                  </View>
                  <Text style={T.section}>{t('auth.allReady')}</Text>
                  <Text style={[T.bodySoft, { textAlign: 'center' }]}>
                    {t('auth.allReadySub')}
                  </Text>
                </Card>

                <Card>
                  <Text style={T.label}>{t('auth.marketReadiness')}</Text>
                  {readiness.map((r) => (
                    <View key={r.channel} style={{ flexDirection: 'row', gap: S.sm,
                                                   alignItems: 'center', paddingVertical: 6 }}>
                      {r.ready ? <Check color={C.money} size={18} />
                               : <Warning color={C.gold} size={18} />}
                      <Text style={[T.bodySoft, { flex: 1, color: C.ink }]}>{r.channel}</Text>
                      {r.ready ? <Pill text={t('create.ready')} tone="good" />
                               : <Pill text={t('auth.nLeft', { n: r.missing.length })} tone="warn" />}
                    </View>
                  ))}
                </Card>

                <Btn label={t('auth.backToListing')} tone="money" large
                     icon={<Arrow color={C.white} size={22} />}
                     onPress={() => { if (artisan) onAuthed(artisan); onClose(); }} />
              </>
            ) : null}
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}
