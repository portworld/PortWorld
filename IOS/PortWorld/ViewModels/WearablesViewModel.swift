// WearablesViewModel.swift
//
// Primary view model for the CameraAccess app that manages DAT SDK integration.
// Demonstrates how to listen to device availability changes using the DAT SDK's
// device stream functionality and handle permission requests.

import Combine
import Foundation
import MWDATCore
import SwiftUI

@MainActor
class WearablesViewModel: ObservableObject {
  @Published var devices: [DeviceIdentifier]
  @Published var registrationState: RegistrationState
  @Published var showGettingStartedSheet: Bool = false
  @Published var showError: Bool = false
  @Published var errorMessage: String = ""
  @Published var isMockModeEnabled: Bool
  @Published var isMockDeviceReady: Bool
  @Published var isPreparingMockDevice: Bool

  var canEnterSession: Bool {
    registrationState == .registered || isMockDeviceReady
  }

  private var registrationTask: Task<Void, Never>?
  private var deviceStreamTask: Task<Void, Never>?
  private var setupDeviceStreamTask: Task<Void, Never>?
  private let wearables: WearablesInterface
  private let mockDeviceController: MockDeviceController
  private var compatibilityListenerTokens: [DeviceIdentifier: AnyListenerToken] = [:]

  #if DEBUG
    private let mockModePreferenceKey = "portworld.debug.mockModeEnabled"
  #endif

  init(wearables: WearablesInterface, mockDeviceController: MockDeviceController? = nil) {
    self.wearables = wearables
    self.mockDeviceController = mockDeviceController ?? MockDeviceController()
    self.devices = wearables.devices
    self.registrationState = wearables.registrationState
    self.isMockModeEnabled = false
    self.isMockDeviceReady = self.mockDeviceController.isEnabled
    self.isPreparingMockDevice = false

    setupDeviceStreamTask = Task {
      await setupDeviceStream()
    }

    registrationTask = Task { [weak self] in
      guard let self else { return }
      for await registrationState in self.wearables.registrationStateStream() {
        let previousState = self.registrationState
        self.registrationState = registrationState
        if self.showGettingStartedSheet == false && registrationState == .registered && previousState == .registering {
          self.showGettingStartedSheet = true
        }
      }
    }

    #if DEBUG
      if UserDefaults.standard.bool(forKey: mockModePreferenceKey) {
        Task { @MainActor [weak self] in
          await self?.enableMockMode()
        }
      }
    #endif
  }

  deinit {
    registrationTask?.cancel()
    deviceStreamTask?.cancel()
    setupDeviceStreamTask?.cancel()
  }

  private func setupDeviceStream() async {
    if let task = deviceStreamTask, !task.isCancelled {
      task.cancel()
    }

    deviceStreamTask = Task { [weak self] in
      guard let self else { return }
      for await devices in self.wearables.devicesStream() {
        self.devices = devices
        // Monitor compatibility for each device
        self.monitorDeviceCompatibility(devices: devices)
      }
    }
  }

  private func monitorDeviceCompatibility(devices: [DeviceIdentifier]) {
    // Remove listeners for devices that are no longer present
    let deviceSet = Set(devices)
    compatibilityListenerTokens = compatibilityListenerTokens.filter { deviceSet.contains($0.key) }

    // Add listeners for new devices
    for deviceId in devices {
      guard compatibilityListenerTokens[deviceId] == nil else { continue }
      guard let device = wearables.deviceForIdentifier(deviceId) else { continue }

      // Capture device name before the closure to avoid Sendable issues
      let deviceName = device.nameOrId()
      let token = device.addCompatibilityListener { [weak self] compatibility in
        guard let self else { return }
        if compatibility == .deviceUpdateRequired {
          Task { @MainActor in
            self.showError("Device '\(deviceName)' requires an update to work with this app")
          }
        }
      }
      compatibilityListenerTokens[deviceId] = token
    }
  }

  func connectGlasses() {
    guard registrationState != .registering else { return }
    Task { @MainActor in
      do {
        try await wearables.startRegistration()
      } catch let error as RegistrationError {
        showError(error.description)
      } catch {
        showError(error.localizedDescription)
      }
    }
  }

  func disconnectGlasses() {
    Task { @MainActor in
      do {
        try await wearables.startUnregistration()
      } catch let error as UnregistrationError {
        showError(error.description)
      } catch {
        showError(error.localizedDescription)
      }
    }
  }

  func showError(_ error: String) {
    errorMessage = error
    showError = true
  }

  func enableMockMode() async {
    guard !isPreparingMockDevice else { return }
    isPreparingMockDevice = true
    defer { isPreparingMockDevice = false }

    do {
      try await mockDeviceController.enableMockDevice()
      isMockModeEnabled = true
      isMockDeviceReady = mockDeviceController.isEnabled
      #if DEBUG
        UserDefaults.standard.set(true, forKey: mockModePreferenceKey)
      #endif
    } catch {
      isMockModeEnabled = false
      isMockDeviceReady = false
      #if DEBUG
        UserDefaults.standard.set(false, forKey: mockModePreferenceKey)
      #endif
      showError("Unable to enable mock device: \(error.localizedDescription)")
    }
  }

  func disableMockMode() {
    mockDeviceController.disableMockDevice()
    isMockModeEnabled = false
    isMockDeviceReady = mockDeviceController.isEnabled
    #if DEBUG
      UserDefaults.standard.set(false, forKey: mockModePreferenceKey)
    #endif
  }

  func toggleMockMode() async {
    if isMockModeEnabled {
      disableMockMode()
    } else {
      await enableMockMode()
    }
  }

  func dismissError() {
    showError = false
  }

  func handleMetaCallback(url: URL) async {
    guard
      let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
      components.queryItems?.contains(where: { $0.name == "metaWearablesAction" }) == true
    else {
      return
    }

    do {
      _ = try await wearables.handleUrl(url)
    } catch {
      showError(error.localizedDescription)
    }
  }
}
