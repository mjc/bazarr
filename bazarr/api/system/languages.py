# coding=utf-8

from flask_restx import Resource, Namespace, reqparse

from app.database import TableHistory, TableHistoryMovie, TableSettingsLanguages, database, select
from languages.get_languages import get_language_registry
from languages.formatting import LanguageInfo

from ..utils import authenticate, False_Keys

api_ns_system_languages = Namespace('System Languages', description='Get languages list')


class MenuLanguage(LanguageInfo):
    enabled: bool


@api_ns_system_languages.route('system/languages')
class Languages(Resource):
    get_request_parser = reqparse.RequestParser()
    get_request_parser.add_argument('history', type=str, required=False, help='Language name for history stats')

    @authenticate
    @api_ns_system_languages.expect(get_request_parser)
    @api_ns_system_languages.response(200, 'Success')
    @api_ns_system_languages.response(401, 'Not Authenticated')
    def get(self) -> list[MenuLanguage]:
        """List languages for history filter or for language filter menu"""
        args = self.get_request_parser.parse_args()
        history = args.get('history')
        if history and history not in False_Keys:
            languages_dicts: list[MenuLanguage] = []
            seen_codes: set[str] = set()
            registry = get_language_registry()
            for table in (TableHistory, TableHistoryMovie):
                column = table.__table__.c.language
                statement = select(column).where(column.is_not(None))
                with database.execute(statement) as rows:
                    for token in rows.yield_per(500).scalars():
                        language = token.split(':', 1)[0]
                        code2 = None
                        if len(language) == 2:
                            if language in seen_codes:
                                continue
                            code2 = language
                            record = registry.from_code2(language)
                        elif len(language) == 3:
                            record = registry.from_code3(language)
                            code2 = record.code2 if record is not None else None
                        else:
                            continue

                        if code2 is not None and code2 not in seen_codes:
                            languages_dicts.append({
                                'code2': code2,
                                'code3': record.code3 if record is not None else None,
                                'name': record.name if record is not None else None,
                                'enabled': False,
                            })
                            seen_codes.add(code2)
        else:
            languages_dicts = [{
                'name': x.name,
                'code2': x.code2,
                'code3': x.code3,
                'enabled': x.enabled == 1
            } for x in database.execute(
                select(TableSettingsLanguages.name,
                       TableSettingsLanguages.code2,
                       TableSettingsLanguages.code3,
                       TableSettingsLanguages.enabled)
                .order_by(TableSettingsLanguages.name))
                .all()]

        return sorted(languages_dicts, key=lambda language: language['name'] or '')
