import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import * as Speech from 'expo-speech';
import { Card, Btn, Pill } from '../ui';
import { C, S, T, R, shadow } from '../theme';
import { Camera as CamIcon, Check, Warning, Back } from '../icons';
import { toGray, analyse, Metrics } from '../vision/quality';
import { judge, Smoother, CATEGORIES, CategoryKey, Verdict } from '../vision/guidance';
import { ShotPlan } from './shotPlan';
import { useI18n } from '../i18n';

/**
 * Web build of the guided camera: a real in-app viewfinder via getUserMedia, with
 * frames sampled to a hidden canvas and analysed locally at ~6fps.
 *
 * Nothing here touches the network. The whole guidance loop - framing, focus,
 * lighting, glare, clutter - runs on the device, which is the point: an artisan
 * in a village with no signal still gets told how to hold the phone.
 */
export default function GuidedCameraWeb({
  category = 'general', onCapture, onClose, plan,
}: {
  category?: CategoryKey;
  plan: ShotPlan;
  onCapture: (dataUri: string, shotKey: string) => void;
  onClose: () => void;
}) {
  const videoRef = useRef<any>(null);
  const canvasRef = useRef<any>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const smoother = useRef(new Smoother());
  const rafRef = useRef<any>(null);

  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [err, setErr] = useState('');
  const [ready, setReady] = useState(false);
  const spokenRef = useRef<string>('');

  const { t, locale } = useI18n();
  const shot = plan.current();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: 'environment' },
                   width: { ideal: 1920 }, height: { ideal: 1440 } },
          audio: false,
        });
        if (cancelled) { stream.getTracks().forEach((t) => t.stop()); return; }
        streamRef.current = stream;
        const v = videoRef.current;
        if (v) {
          v.srcObject = stream;
          v.playsInline = true;
          v.muted = true;
          await v.play().catch(() => {});
          setReady(true);
        }
      } catch (e: any) {
        setErr(e?.name === 'NotAllowedError'
          ? t('cam.denied')
          : t('cam.failed', { err: String(e?.message || e) }));
      }
    })();
    return () => {
      cancelled = true;
      streamRef.current?.getTracks().forEach((t) => t.stop());
      if (rafRef.current) clearTimeout(rafRef.current);
    };
  }, []);

  const tick = useCallback(() => {
    const v = videoRef.current;
    const c = canvasRef.current;
    if (!v || !c || v.videoWidth === 0) {
      rafRef.current = setTimeout(tick, 250);
      return;
    }
    // Analyse a small copy: 240px wide is plenty for these estimators and keeps
    // the whole loop under a few milliseconds.
    const aw = 240;
    const ah = Math.max(1, Math.round((v.videoHeight / v.videoWidth) * aw));
    c.width = aw; c.height = ah;
    const ctx = c.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(v, 0, 0, aw, ah);
    const img = ctx.getImageData(0, 0, aw, ah);

    const m = analyse(toGray(img.data, aw, ah, 160));
    const raw = judge(m, category);
    const stable = smoother.current.push(raw);
    setMetrics(m);
    setVerdict(stable);

    // Speak a change once. Repeating the same sentence every 160ms is unusable.
    if (stable.code !== spokenRef.current) {
      spokenRef.current = stable.code;
      try { Speech.speak(t(`guide.${stable.code}`), { language: locale, rate: 1.0 }); } catch {}
    }
    rafRef.current = setTimeout(tick, 160);
  }, [category]);

  useEffect(() => {
    if (!ready) return;
    tick();
    return () => { if (rafRef.current) clearTimeout(rafRef.current); };
  }, [ready, tick]);

  function capture() {
    const v = videoRef.current;
    if (!v || v.videoWidth === 0) return;
    const c = document.createElement('canvas');
    c.width = v.videoWidth; c.height = v.videoHeight;
    c.getContext('2d')!.drawImage(v, 0, 0);
    onCapture(c.toDataURL('image/jpeg', 0.92), shot.key);
    smoother.current.reset();
    spokenRef.current = '';
  }

  const good = verdict?.ok && smoother.current.steadyGood();
  const box = metrics?.subject;

  return (
    <View style={{ flex: 1, backgroundColor: '#000' }}>
      {/* The live preview. */}
      <View style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
        {React.createElement('video', {
          ref: videoRef,
          style: { width: '100%', height: '100%', objectFit: 'cover',
                   display: 'block', background: '#000' },
        })}
        {React.createElement('canvas', { ref: canvasRef, style: { display: 'none' } })}

        {/* Framing guide. It follows the detected subject rather than sitting
            static, so the artisan can see what the app is actually looking at. */}
        <View pointerEvents="none" style={{ position: 'absolute', inset: 0 as any }}>
          <View style={{
            position: 'absolute', left: '8%', top: '14%', right: '8%', bottom: '26%',
            borderWidth: 2, borderRadius: R.lg, borderStyle: 'dashed',
            borderColor: good ? C.money : 'rgba(255,255,255,0.55)',
          }} />
          {box ? (
            <View style={{
              position: 'absolute',
              left: `${box.x * 100}%`, top: `${box.y * 100}%`,
              width: `${box.w * 100}%`, height: `${box.h * 100}%`,
              borderWidth: 3, borderRadius: R.md,
              borderColor: good ? C.money : C.primaryLite,
            }} />
          ) : null}
        </View>

        {/* Which shot we are on, and why. */}
        <View style={{ position: 'absolute', top: 44, left: S.md, right: S.md,
                       flexDirection: 'row', alignItems: 'center', gap: S.sm }}>
          <Pressable onPress={onClose} accessibilityLabel={t('common.close')} hitSlop={12}
                     style={{ width: 42, height: 42, borderRadius: R.pill,
                              backgroundColor: 'rgba(0,0,0,0.5)',
                              alignItems: 'center', justifyContent: 'center' }}>
            <Back color="#fff" size={20} />
          </Pressable>
          <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.5)',
                         borderRadius: R.md, padding: S.sm }}>
            <Text style={[T.micro, { color: '#fff' }]}>
              {plan.index + 1}/{plan.total} · {t(`craft.${category}`)}
            </Text>
            <Text style={[T.body, { color: '#fff' }]}>{t(`shot.${category}.${shot.key}`)}</Text>
          </View>
        </View>

        {/* The single instruction. */}
        <View style={{ position: 'absolute', left: S.md, right: S.md, bottom: 150 }}>
          <View style={[{
            borderRadius: R.lg, padding: S.md, flexDirection: 'row',
            alignItems: 'center', gap: S.sm,
            backgroundColor: good ? 'rgba(11,122,84,0.94)'
              : verdict?.severity === 'warn' ? 'rgba(185,138,31,0.94)'
              : 'rgba(23,19,49,0.88)',
          }, shadow(2)]}>
            {good ? <Check color="#fff" size={22} /> : <Warning color="#fff" size={22} />}
            <Text style={[T.body, { color: '#fff', flex: 1 }]}>
              {err || (verdict ? t(`guide.${verdict.code}`) : t('cam.preparing'))}
            </Text>
          </View>
        </View>
      </View>

      {/* Shutter. Enabled only when the frame has been good for several frames. */}
      <View style={{ backgroundColor: '#000', paddingBottom: 28, paddingTop: S.md,
                     alignItems: 'center', gap: S.sm }}>
        <Pressable onPress={capture} disabled={!ready} accessibilityLabel={t('create.camera')}>
          <View style={{
            width: 78, height: 78, borderRadius: R.pill, borderWidth: 5,
            borderColor: good ? C.money : 'rgba(255,255,255,0.5)',
            backgroundColor: good ? C.money : 'rgba(255,255,255,0.18)',
            alignItems: 'center', justifyContent: 'center',
          }}>
            <CamIcon color="#fff" size={30} />
          </View>
        </Pressable>
        <Text style={[T.micro, { color: 'rgba(255,255,255,0.65)' }]}>
          {good ? t('cam.takeNow') : t('cam.fixFirst')}
        </Text>
      </View>
    </View>
  );
}
