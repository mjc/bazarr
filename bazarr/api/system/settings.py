# coding=utf-8

import json

from flask import request, jsonify
from flask_restx import Resource, Namespace
from dynaconf.validator import ValidationError

from api.utils import None_Keys
from app.database import TableLanguagesProfiles, TableSettingsLanguages, TableSettingsNotifier, \
    load_profile_catalog, invalidate_profiles, database, insert, update, delete, select
from app.event_handler import event_stream
from app.language_profiles import prepare_profile_sources
from app.config import settings, save_settings, get_settings
from app.scheduler import scheduler
from subtitles.indexer.series import list_missing_subtitles
from subtitles.indexer.movies import list_missing_subtitles_movies

from ..utils import authenticate

api_ns_system_settings = Namespace('systemSettings', description='System settings API endpoint')


@api_ns_system_settings.hide
@api_ns_system_settings.route('system/settings')
class SystemSettings(Resource):
    @authenticate
    def get(self):
        data = get_settings()
        data['notifications'] = dict()
        data['notifications']['providers'] = [{
            'name': x.name,
            'enabled': x.enabled == 1,
            'url': x.url
        } for x in database.execute(
            select(TableSettingsNotifier.name,
                   TableSettingsNotifier.enabled,
                   TableSettingsNotifier.url)
            .order_by(TableSettingsNotifier.name))
            .all()]

        return jsonify(data)

    @authenticate
    def post(self):
        languages_profiles = request.form.get('languages-profiles')
        profiles_to_save = json.loads(languages_profiles) if languages_profiles else None
        profile_sources = ()
        if profiles_to_save is not None:
            try:
                profile_sources = prepare_profile_sources(profiles_to_save, load_profile_catalog())
            except ValueError as error:
                return str(error), 400
        enabled_languages = request.form.getlist('languages-enabled')
        if len(enabled_languages) != 0:
            database.execute(
                update(TableSettingsLanguages)
                .values(enabled=0))
            for code in enabled_languages:
                database.execute(
                    update(TableSettingsLanguages)
                    .values(enabled=1)
                    .where(TableSettingsLanguages.code2 == code))
            event_stream("languages")

        if profiles_to_save is not None:
            existing_ids = set(database.execute(select(TableLanguagesProfiles.profileId)).scalars())
            for source in profile_sources:
                values: dict[str, str | int | None] = {
                    'name': source.name, 'cutoff': source.cutoff, 'items': source.items,
                    'mustContain': source.must_contain, 'mustNotContain': source.must_not_contain,
                    'originalFormat': source.original_format, 'tag': source.tag, 'profileType': source.profile_type,
                }
                if source.profile_id in existing_ids:
                    database.execute(update(TableLanguagesProfiles).values(**values)
                                     .where(TableLanguagesProfiles.profileId == source.profile_id))
                    existing_ids.remove(source.profile_id)
                else:
                    database.execute(insert(TableLanguagesProfiles).values(profileId=source.profile_id, **values))
            if existing_ids:
                database.execute(delete(TableLanguagesProfiles).where(TableLanguagesProfiles.profileId.in_(existing_ids)))
            invalidate_profiles()

            event_stream("languages")

            if settings.general.use_sonarr:
                list_missing_subtitles()
            if settings.general.use_radarr:
                list_missing_subtitles_movies()

        # Update Notification
        notifications = request.form.getlist('notifications-providers')
        for item in notifications:
            item = json.loads(item)
            database.execute(
                update(TableSettingsNotifier).values(
                    enabled=int(item['enabled'] is True),
                    url=item['url'])
                .where(TableSettingsNotifier.name == item['name']))

        try:
            save_settings(zip(request.form.keys(), request.form.listvalues()))
        except ValidationError as e:
            event_stream("settings")
            return e.message, 406
        else:
            event_stream("settings")
            return '', 204


@api_ns_system_settings.route('system/webhooks/test')
class SystemWebhookTest(Resource):
    @authenticate
    def post(self):
        """Test external webhook connection."""
        try:
            from utilities.autopulse_webhook import test_external_webhook_connection
            
            success, message = test_external_webhook_connection()
            
            return {
                'data': {
                    'success': success,
                    'message': message
                }
            }
            
        except Exception as e:
            return {
                'data': {
                    'success': False,
                    'message': f'Failed to test webhook: {str(e)}'
                }
            }, 400
