#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Datei: /opt/ai/oroma/v2.11/wrappers/degirum_wrapper.py
Beschreibung:
    Wrapper für die DeGirum PySDK (Alternative NPU zu Hailo).
    Ermöglicht die Nutzung des DeGirum ModelZoo (lokal oder Cloud),
    vereinheitlicht die Schnittstelle zu ORÓMA (predict(), load_model(), close()).

Abhängigkeiten:
    pip install degirum
    -> ggf. Systemlibs für NPU-Treiber, siehe: https://docs.degirum.com/pysdk/quickstart

Autor: ORÓMA v2.11
"""

import os
import logging
import degirum as dg

logger = logging.getLogger("degirum_wrapper")
logger.setLevel(logging.INFO)


class DeGirumWrapper:
    """
    Einheitlicher Wrapper für DeGirum NPU.
    """

    def __init__(self, zoo_dir: str = None, use_cloud: bool = False, cloud_url: str = None):
        """
        Initialisierung
        :param zoo_dir: Pfad zum lokalen ModelZoo-Cache
        :param use_cloud: True → Modelle aus Cloud laden
        :param cloud_url: URL des Cloud ModelZoos (falls None → Default)
        """
        self.zoo_dir = zoo_dir or os.environ.get("DEGIRUM_ZOO", "/opt/ai/oroma/v2.11/models/degirum")
        self.use_cloud = use_cloud
        self.cloud_url = cloud_url
        self.device = None
        self.model = None

        if not self.use_cloud:
            os.makedirs(self.zoo_dir, exist_ok=True)
            logger.info(f"[DeGirum] Lokaler Zoo wird verwendet: {self.zoo_dir}")
        else:
            logger.info(f"[DeGirum] Cloud-Zoo wird verwendet: {self.cloud_url or 'default'}")

    def connect(self):
        """Verbindung zum Gerät / Cloud herstellen"""
        try:
            if self.use_cloud:
                self.device = dg.connect(self.cloud_url or dg.CLOUD_URL)
                logger.info("[DeGirum] Verbunden mit Cloud Device")
            else:
                self.device = dg.connect(dg.CLOUD_URL_LOCAL, zoo_dir=self.zoo_dir)
                logger.info("[DeGirum] Verbunden mit lokalem Device")
        except Exception as e:
            logger.error(f"[DeGirum] Fehler beim Verbinden: {e}")
            raise

    def load_model(self, model_name: str, zoo: str = None):
        """
        Lädt ein Modell aus dem Zoo
        :param model_name: z. B. "yolov5m"
        :param zoo: optionaler Zoo-Name
        """
        if self.device is None:
            self.connect()

        try:
            logger.info(f"[DeGirum] Lade Modell: {model_name}")
            self.model = self.device.load_model(model_name, zoo=zoo)
            return self.model
        except Exception as e:
            logger.error(f"[DeGirum] Fehler beim Laden des Modells {model_name}: {e}")
            raise

    def infer(self, input_data):
        """
        Führt eine Inferenz durch
        :param input_data: numpy.ndarray oder Bildpfad
        :return: Ergebnis-Objekt
        """
        if self.model is None:
            raise RuntimeError("Kein Modell geladen. Bitte load_model() zuerst aufrufen.")

        try:
            result = self.model(input_data)
            logger.debug(f"[DeGirum] Ergebnis: {result}")
            return result
        except Exception as e:
            logger.error(f"[DeGirum] Fehler bei Inferenz: {e}")
            raise

    def close(self):
        """Trennt die Verbindung"""
        try:
            if self.device:
                self.device.close()
                logger.info("[DeGirum] Verbindung geschlossen")
        except Exception as e:
            logger.warning(f"[DeGirum] Fehler beim Schließen: {e}")


# Quick-Test (nur wenn direkt aufgerufen)
if __name__ == "__main__":
    import numpy as np

    wrapper = DeGirumWrapper()
    wrapper.connect()
    # Beispielmodell – abhängig vom Zoo (hier YOLOv5m)
    model = wrapper.load_model("yolov5m")
    dummy = np.zeros((640, 640, 3), dtype=np.uint8)
    res = wrapper.infer(dummy)
    print("Dummy-Inferenz:", res)
    wrapper.close()