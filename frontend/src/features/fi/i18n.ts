"use client";

import type { Persona } from "./types";
import { PERSONAS } from "./personas";
import { usePrefs } from "./stores";

export type Language = "en" | "hi" | "es";
export const LANGUAGE_NAMES: Record<Language, string> = { en: "English", hi: "हिन्दी", es: "Español" };

type PersonaText = { label: string; blurb: string; priorities: Array<{ title: string; why: string }> };

const en = {
  steps: ["How you watch", "Your club", "Your players"],
  language: "Language",
  howWatch: "How do you watch football?",
  howWatchHelp: "This sets what Watch and Stats show first, and the colours you see. Switch any time from the header.",
  firstOnStats: "First on Stats",
  pickClub: "Pick your club",
  pickClubHelp: "The whole app takes on their colours, and we lead with their side of the story.",
  neutral: "No club — I'm neutral",
  followPlayers: "Follow your favourite players",
  upTo: (n: number) => `Up to ${n}.`,
  fantasyHelp: "These become your fantasy squad on Stats.",
  followHelp: "Their moments and numbers float to the top.",
  picked: (a: number, b: number) => `${a}/${b} picked`,
  squad: (c: string) => `${c} squad`,
  otherPlayers: "Other players",
  tonightPlayers: "Tonight's players",
  noSquad: (c: string) => `${c} squad isn't published yet — pick from tonight's match instead.`,
  back: "Back",
  continue: "Continue",
  start: "Start watching",
  skip: "Skip for now",
  watch: "Watch",
  stats: "Stats",
  orderedFor: "Ordered for you",
  whereWeStand: (club: string) => `${club}: where we stand`,
  battles: (pos: number, won: number, total: number) => `${pos}% of the ball · ${won} of ${total} battles won`,
  playersYouFollow: "Players you follow",
  differentials: "Differentials you don't own",
  cleanSheet: "Clean sheet watch",
  glossary: "What the numbers mean",
  topRated: "Top rated so far",
  moreStats: "More stats",
  fewerStats: "Fewer stats",
  shotMap: "Shot map",
  playerStats: "Player stats",
  videoMissing: "Match video not loaded",
  videoHelp: "Run scripts/fetch_soccernet_video.py with your SoccerNet NDA password to add the footage.",
};
type Dict = typeof en;

const hi: Dict = {
  steps: ["आप कैसे देखते हैं", "आपका क्लब", "आपके खिलाड़ी"],
  language: "भाषा",
  howWatch: "आप फ़ुटबॉल कैसे देखते हैं?",
  howWatchHelp: "इससे तय होता है कि वॉच और आँकड़ों में पहले क्या दिखे और रंग कैसे हों। हेडर से कभी भी बदलें।",
  firstOnStats: "आँकड़ों में पहले",
  pickClub: "अपना क्लब चुनें",
  pickClubHelp: "पूरा ऐप उनके रंगों में दिखेगा, और कहानी उनकी तरफ़ से शुरू होगी।",
  neutral: "कोई क्लब नहीं — मैं तटस्थ हूँ",
  followPlayers: "अपने पसंदीदा खिलाड़ी फ़ॉलो करें",
  upTo: (n) => `अधिकतम ${n}।`,
  fantasyHelp: "ये आँकड़ों में आपकी फ़ैंटेसी टीम बनेंगे।",
  followHelp: "इनके पल और आँकड़े सबसे ऊपर दिखेंगे।",
  picked: (a, b) => `${a}/${b} चुने गए`,
  squad: (c) => `${c} टीम`,
  otherPlayers: "अन्य खिलाड़ी",
  tonightPlayers: "आज रात के खिलाड़ी",
  noSquad: (c) => `${c} की टीम अभी जारी नहीं हुई — आज रात के मैच से चुनें।`,
  back: "पीछे",
  continue: "आगे",
  start: "देखना शुरू करें",
  skip: "अभी छोड़ें",
  watch: "देखें",
  stats: "आँकड़े",
  orderedFor: "आपके लिए क्रम",
  whereWeStand: (club) => `${club}: हम कहाँ हैं`,
  battles: (pos, won, total) => `${pos}% गेंद पर कब्ज़ा · ${total} में से ${won} मुकाबले जीते`,
  playersYouFollow: "आपके फ़ॉलो किए खिलाड़ी",
  differentials: "जो खिलाड़ी आपके पास नहीं हैं",
  cleanSheet: "क्लीन शीट पर नज़र",
  glossary: "आँकड़ों का मतलब",
  topRated: "अब तक सबसे अच्छी रेटिंग",
  moreStats: "और आँकड़े",
  fewerStats: "कम आँकड़े",
  shotMap: "शॉट मैप",
  playerStats: "खिलाड़ी आँकड़े",
  videoMissing: "मैच वीडियो लोड नहीं हुआ",
  videoHelp: "फ़ुटेज जोड़ने के लिए अपने SoccerNet NDA पासवर्ड के साथ scripts/fetch_soccernet_video.py चलाएँ।",
};

const es: Dict = {
  steps: ["Cómo ves", "Tu club", "Tus jugadores"],
  language: "Idioma",
  howWatch: "¿Cómo ves el fútbol?",
  howWatchHelp: "Define qué ves primero en Ver y Estadísticas, y los colores de la app. Cámbialo cuando quieras desde la cabecera.",
  firstOnStats: "Primero en Estadísticas",
  pickClub: "Elige tu club",
  pickClubHelp: "Toda la app usa sus colores y contamos el partido desde su lado.",
  neutral: "Sin club — soy neutral",
  followPlayers: "Sigue a tus jugadores favoritos",
  upTo: (n) => `Hasta ${n}.`,
  fantasyHelp: "Serán tu equipo fantasy en Estadísticas.",
  followHelp: "Sus jugadas y números salen primero.",
  picked: (a, b) => `${a}/${b} elegidos`,
  squad: (c) => `Plantilla de ${c}`,
  otherPlayers: "Otros jugadores",
  tonightPlayers: "Jugadores de esta noche",
  noSquad: (c) => `La plantilla de ${c} aún no está publicada — elige del partido de esta noche.`,
  back: "Atrás",
  continue: "Continuar",
  start: "Empezar a ver",
  skip: "Omitir por ahora",
  watch: "Ver",
  stats: "Estadísticas",
  orderedFor: "Ordenado para ti",
  whereWeStand: (club) => `${club}: cómo vamos`,
  battles: (pos, won, total) => `${pos}% de posesión · ${won} de ${total} duelos ganados`,
  playersYouFollow: "Jugadores que sigues",
  differentials: "Diferenciales que no tienes",
  cleanSheet: "Portería a cero",
  glossary: "Qué significan los números",
  topRated: "Mejor valorado",
  moreStats: "Más estadísticas",
  fewerStats: "Menos estadísticas",
  shotMap: "Mapa de tiros",
  playerStats: "Estadísticas de jugadores",
  videoMissing: "Vídeo del partido no cargado",
  videoHelp: "Ejecuta scripts/fetch_soccernet_video.py con tu contraseña NDA de SoccerNet para añadir las imágenes.",
};

const PERSONA_TEXT: Record<Exclude<Language, "en">, Record<Persona, PersonaText>> = {
  hi: {
    diehard: { label: "कट्टर प्रशंसक", blurb: "पहले आपका क्लब: मौके, गोल खाए और क्यों", priorities: [
      { title: "क्या मेरा क्लब जीत रहा है?", why: "स्कोर, मौके और xG — हम बनाम वे" },
      { title: "ख़तरनाक पल", why: "दोनों तरफ़ लक्ष्य पर शॉट, बड़े मौके और बचाव" },
      { title: "जुझारूपन", why: "डुएल, दूरी और स्प्रिंट" },
      { title: "मेरे खिलाड़ी", why: "जिन्हें मैं फ़ॉलो करता हूँ उनका हाल" },
      { title: "अनुशासन", why: "कार्ड और फ़ाउल" },
    ] },
    casual: { label: "आम दर्शक", blurb: "बस बड़े पल, आसान शब्दों में", priorities: [
      { title: "कौन आगे है?", why: "कब्ज़ा और शॉट, बस" },
      { title: "मैच का सितारा", why: "अब तक का सबसे अच्छा खिलाड़ी" },
      { title: "कमाल के आँकड़े", why: "सबसे तेज़ दौड़, सबसे ज़ोरदार शॉट" },
      { title: "जाने-पहचाने खिलाड़ी", why: "मेरे चुने नाम" },
      { title: "कोई जटिल शब्द नहीं", why: "उन्नत आँकड़े छिपे और समझाए हुए" },
    ] },
    tactical: { label: "रणनीतिक", blurb: "तरीके, प्रेसिंग और शेप — सबूत के साथ", priorities: [
      { title: "मौकों की गुणवत्ता", why: "xG, xGOT, ओपन प्ले बनाम सेट पीस" },
      { title: "बिल्ड-अप", why: "पास, प्रोग्रेसिव पास, लंबी गेंदें" },
      { title: "प्रेसिंग और ढाँचा", why: "ऊँचे रीगेन, टैकल, इंटरसेप्शन" },
      { title: "डुएल", why: "ज़मीनी और हवाई डुएल, ड्रिबल" },
      { title: "शारीरिक भार", why: "दौड़ और स्प्रिंट भार" },
    ] },
    fantasy: { label: "फ़ैंटेसी मैनेजर", blurb: "आपके खिलाड़ी, उनके पॉइंट, तुरंत", priorities: [
      { title: "मेरी टीम के पॉइंट", why: "हर खिलाड़ी के लाइव पॉइंट" },
      { title: "आक्रमण रिटर्न", why: "गोल, असिस्ट, xG और शॉट" },
      { title: "क्लीन शीट", why: "गोल खाए, बचाव, शॉट झेले" },
      { title: "जोखिम", why: "कार्ड, मिनट और बदलाव" },
      { title: "डिफ़रेंशियल", why: "अच्छे खिलाड़ी जो मेरे पास नहीं" },
    ] },
  },
  es: {
    diehard: { label: "Hincha", blurb: "Tu club primero: ocasiones, goles en contra y por qué", priorities: [
      { title: "¿Va ganando mi club?", why: "Marcador, ocasiones y xG nuestro contra el suyo" },
      { title: "Momentos de peligro", why: "Tiros a puerta, ocasiones claras y paradas" },
      { title: "Lucha y esfuerzo", why: "Duelos, distancia y sprints" },
      { title: "Mis jugadores", why: "Cómo van los que sigo" },
      { title: "Disciplina", why: "Tarjetas y faltas" },
    ] },
    casual: { label: "Espectador casual", blurb: "Solo los grandes momentos, en palabras simples", priorities: [
      { title: "¿Quién domina?", why: "Posesión y tiros, nada más" },
      { title: "La estrella", why: "El mejor jugador hasta ahora" },
      { title: "Números wow", why: "Sprint más rápido, tiro más fuerte" },
      { title: "Jugadores que conozco", why: "Los nombres que elegí" },
      { title: "Sin jerga", why: "Estadísticas avanzadas ocultas y explicadas" },
    ] },
    tactical: { label: "Táctico", blurb: "Mecanismos, presión y estructura — con pruebas", priorities: [
      { title: "Calidad de ocasiones", why: "xG, xGOT, jugada vs balón parado" },
      { title: "Construcción", why: "Pases, pases progresivos, balones largos" },
      { title: "Presión y estructura", why: "Recuperaciones altas, entradas, intercepciones" },
      { title: "Duelos", why: "Duelos a ras y aéreos, regates" },
      { title: "Carga física", why: "Carrera y sprints" },
    ] },
    fantasy: { label: "Mánager fantasy", blurb: "Tus jugadores y sus puntos, al momento", priorities: [
      { title: "Puntos de mi equipo", why: "Puntos provisionales en vivo" },
      { title: "Producción ofensiva", why: "Goles, asistencias, xG y tiros" },
      { title: "Portería a cero", why: "Goles en contra, paradas, tiros recibidos" },
      { title: "Riesgos", why: "Tarjetas, minutos y cambios" },
      { title: "Diferenciales", why: "Jugadores destacados que no tengo" },
    ] },
  },
};

const DICTS: Record<Language, Dict> = { en, hi, es };

export function useT() {
  return DICTS[usePrefs((s) => s.language)];
}

export function usePersonaText(persona: Persona): PersonaText {
  const language = usePrefs((s) => s.language);
  return language === "en" ? PERSONAS[persona] : PERSONA_TEXT[language][persona];
}

export const translate = (language: Language) => DICTS[language];
export const personaText = (language: Language, persona: Persona): PersonaText =>
  language === "en" ? PERSONAS[persona] : PERSONA_TEXT[language][persona];
