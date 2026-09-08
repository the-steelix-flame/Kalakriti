import React, { useEffect, useRef, useState } from 'react';
import { View, Text, Pressable, ActivityIndicator } from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import * as Speech from 'expo-speech';
import { Btn } from '../ui';
import { C, S, T, R, shadow } from '../theme';
import { Camera as CamIcon, Check, Warning, Back } from '../icons';
import { analyse, toGray, Metrics } from '../vision/quality';
import { judge, Smoother, CATEGORIES, CategoryKey, Verdict } from '../vision/guidance';
import { ShotPlan } from './shotPlan';
import { useI18n } from '../i18n';
import { decodeJpegToRgba, shrinkToBase64 } from '../vision/decode';

/**
 * Native guided camera.
 *
 * The important change from before: this is an in-app viewfinder (expo-camera's
 * CameraView), not `launchCameraAsync`, which handed the artisan off to the phone's
 * own camera app where we could not guide them at all. The overlay, the framing
 * guide and the spoken instructions all sit on top of our own preview.
 *
 * Frame analysis: expo-camera exposes no frame-processor callback (only barcodes),
 * so the analysis loop samples a small picture roughly twice a second with the
 * shutter sound and post-processing skipped, decodes it locally, and runs exactly
 * the same offline estimators the web build uses. It is a lower frame rate than a
 * true frame processor, but it is real on-device analysis with no extra native
 * dependency - and the guidance it produces is identical.
 *
 * The upgrade path, documented in docs/ON-DEVICE.md, is react-native-vision-camera
 * frame processors (CameraX under the hood) plus an ML Kit plugin, which would lift
 * this to 30fps and add true object labels.
 */
export default function GuidedCameraNative({
  category = 'general', onCapture, onClose, plan,
}: {
  category?: CategoryKey;
  plan: ShotPlan;
  onCapture: (uri: string, shotKey: string) => void;
  onClose: () => void;
}) {
  const camRef = useRef<CameraView | null>(null);
  const smoother = useRef(new Smoother());
  const loopRef = useRef<any>(null);
  const busyRef = useRef(false);
  const aliveRef = useRef(true);
  const spokenRef = useRef('');

  const [perm, requestPerm] = useCameraPermissions();
  const [ready, setReady] = useState(false);
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [analysing, setAnalysing] = useState(false);

  const { t, locale } = useI18n();
  const shot = plan.current();

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
      if (loopRef.current) clearTimeout(loopRef.current);
    };
  }, []);

  useEffect(() => {
    if (!perm) return;
    if (!perm.granted && perm.canAskAgain) requestPerm();
  }, [perm]);

  useEffect(() => {
    if (!ready) return;
    const loop = async () => {
      if (!aliveRef.current) return;
      if (!busyRef.current && camRef.current) {
        busyRef.current = true;
        try {
          const pic = await camRef.current.takePictureAsync({
            quality: 0.4,
            skipProcessing: true,   // no orientation/exif work: we only need pixels
            shutterSound: false,
            imageType: 'jpg',
          } as any) as any;
          // Resize natively first: decoding a full-size JPEG in JS costs hundreds of
          // milliseconds, a 240px one costs a few.
          const small = pic?.uri ? await shrinkToBase64(pic.uri, 240) : null;
          if (small && aliveRef.current) {
            const rgba = await decodeJpegToRgba(small, 240);
            if (rgba && aliveRef.current) {
              const m = analyse(toGray(rgba.data, rgba.width, rgba.height, 160));
              const stable = smoother.current.push(judge(m, category));
              setMetrics(m);
              setVerdict(stable);
              if (stable.code !== spokenRef.current) {
                spokenRef.current = stable.code;
                try { Speech.speak(t(`guide.${stable.code}`), { language: locale }); } catch {}
              }
            }
          }
        } catch {
          // A dropped analysis frame is not worth surfacing; the next one retries.
        } finally {
          busyRef.current = false;
        }
      }
      if (aliveRef.current) loopRef.current = setTimeout(loop, 550);
    };
    loop();
    return () => { if (loopRef.current) clearTimeout(loopRef.current); };
  }, [ready, category]);

  async function capture() {
    if (!camRef.current) return;
    setAnalysing(true);
    try {
      const pic = await camRef.current.takePictureAsync({ quality: 0.92 }) as any;
      if (pic?.uri) {
        onCapture(pic.uri, shot.key);
        smoother.current.reset();
        spokenRef.current = '';
      }
    } finally {
      setAnalysing(false);
    }
  }

  if (!perm?.granted) {
    return (
      <View style={{ flex: 1, backgroundColor: C.bg, padding: S.lg, gap: S.md,
                     justifyContent: 'center' }}>
        <Text style={T.title}>{t('cam.permTitle')}</Text>
        <Text style={T.bodySoft}>
          {t('cam.permBody')}
        </Text>
        <Btn label={t('cam.grant')} tone="money" onPress={requestPerm} />
        <Btn label={t('common.back')} tone="ghost" onPress={onClose} />
      </View>
    );
  }

  const good = verdict?.ok && smoother.current.steadyGood();
  const box = metrics?.subject;

  return (
    <View style={{ flex: 1, backgroundColor: '#000' }}>
      <View style={{ flex: 1, position: 'relative' }}>
        <CameraView
          ref={(r) => { camRef.current = r; }}
          style={{ flex: 1 }}
          facing="back"
          onCameraReady={() => setReady(true)}
        />

        <View pointerEvents="none"
              style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 }}>
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
              {verdict ? t(`guide.${verdict.code}`) : t('cam.preparing')}
            </Text>
          </View>
        </View>
      </View>

      <View style={{ backgroundColor: '#000', paddingBottom: 28, paddingTop: S.md,
                     alignItems: 'center', gap: S.sm }}>
        <Pressable onPress={capture} disabled={!ready || analysing}
                   accessibilityLabel={t('create.camera')}>
          <View style={{
            width: 78, height: 78, borderRadius: R.pill, borderWidth: 5,
            borderColor: good ? C.money : 'rgba(255,255,255,0.5)',
            backgroundColor: good ? C.money : 'rgba(255,255,255,0.18)',
            alignItems: 'center', justifyContent: 'center',
          }}>
            {analysing ? <ActivityIndicator color="#fff" />
                       : <CamIcon color="#fff" size={30} />}
          </View>
        </Pressable>
        <Text style={[T.micro, { color: 'rgba(255,255,255,0.65)' }]}>
          {good ? t('cam.takeNow') : t('cam.fixFirst')}
        </Text>
      </View>
    </View>
  );
}
