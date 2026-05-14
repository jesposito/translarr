/* global ApiClient, Dashboard, define, getParameterByName */
// Translarr plugin settings page controller.
//
// Loads/saves the strongly-typed PluginConfiguration via ApiClient,
// and wires the "Test Connection" + "Translate by item id" buttons
// to the in-Emby REST controller registered under /Translarr/.

(function () {
    'use strict';

    // The plugin GUID MUST match Plugin.cs::Id. Emby uses it to
    // route ApiClient.getPluginConfiguration to our configuration.
    var PLUGIN_ID = 'b2de90e4-a7b2-427f-b562-ca58373e3627';

    function loadConfiguration(page) {
        Dashboard.showLoadingMsg();
        ApiClient.getPluginConfiguration(PLUGIN_ID).then(function (config) {
            page.querySelector('#translarrServerUrl').value = config.ServerUrl || '';
            page.querySelector('#translarrTargetLanguage').value = config.TargetLanguage || '';
            page.querySelector('#translarrWebhookSecret').value = config.WebhookSecret || '';
            Dashboard.hideLoadingMsg();
        });
    }

    function saveConfiguration(page, event) {
        event.preventDefault();
        Dashboard.showLoadingMsg();
        ApiClient.getPluginConfiguration(PLUGIN_ID).then(function (config) {
            config.ServerUrl = page.querySelector('#translarrServerUrl').value.trim();
            config.TargetLanguage = page.querySelector('#translarrTargetLanguage').value.trim();
            config.WebhookSecret = page.querySelector('#translarrWebhookSecret').value;
            ApiClient.updatePluginConfiguration(PLUGIN_ID, config).then(function (result) {
                Dashboard.processPluginConfigurationUpdateResult(result);
            });
        });
        return false;
    }

    function pretty(obj) {
        try { return JSON.stringify(obj, null, 2); }
        catch (e) { return String(obj); }
    }

    function testConnection(page) {
        var out = page.querySelector('#translarrTestConnectionResult');
        out.textContent = 'Contacting Translarr server...';
        ApiClient.ajax({
            type: 'GET',
            url: ApiClient.getUrl('Translarr/Health'),
            dataType: 'json',
        }).then(function (response) {
            out.textContent = pretty(response);
        }, function (err) {
            out.textContent = 'Request failed: ' + (err && err.statusText ? err.statusText : 'unknown error');
        });
    }

    function translateItem(page, event) {
        event.preventDefault();
        var out = page.querySelector('#translarrTranslateResult');
        var itemId = page.querySelector('#translarrItemId').value.trim();
        if (!itemId) {
            out.textContent = 'Item id is required.';
            return false;
        }
        var body = {
            ItemId: itemId,
            TargetLang: page.querySelector('#translarrItemTargetLang').value.trim(),
            Force: page.querySelector('#translarrItemForce').checked,
        };
        out.textContent = 'Submitting...';
        ApiClient.ajax({
            type: 'POST',
            url: ApiClient.getUrl('Translarr/Translate'),
            contentType: 'application/json',
            data: JSON.stringify(body),
            dataType: 'json',
        }).then(function (response) {
            out.textContent = pretty(response);
        }, function (err) {
            out.textContent = 'Request failed: ' + (err && err.statusText ? err.statusText : 'unknown error');
        });
        return false;
    }

    document.querySelector('.translarrConfigurationPage').addEventListener('pageshow', function () {
        var page = this;
        loadConfiguration(page);

        var configForm = page.querySelector('.translarrConfigForm');
        configForm.addEventListener('submit', function (ev) { return saveConfiguration(page, ev); });

        var testButton = page.querySelector('#translarrTestConnectionButton');
        testButton.addEventListener('click', function () { testConnection(page); });

        var translateForm = page.querySelector('.translarrTranslateForm');
        translateForm.addEventListener('submit', function (ev) { return translateItem(page, ev); });
    });
})();
